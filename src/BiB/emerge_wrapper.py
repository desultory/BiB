from os import chdir, chroot, environ
from pathlib import Path
from shlex import split
from subprocess import SubprocessError, run
from tomllib import load

from zenlib.logging import loggify
from zenlib.util import colorize as c_

from .mount_mixins import MountMixins

DEFAULT_FEATURES = set(["buildpkg", "binpkg-multi-instance"])


@loggify
class EmergeWrapper(MountMixins):
    def __init__(self, config_file=None, *args, **kwargs):
        self.load_config(config_file=config_file or "config.toml")

        if not self.pkgdir.exists():
            self.logger.info(f"Creating package directory: {c_(self.pkgdir, 'blue')}")
            self.pkgdir.mkdir(parents=True, exist_ok=True)

    def load_config(self, config_file="config.toml"):
        """Reads the config file into self.config

        Ensures that the following config values are defined:
            - name (default: "default")  The name of this config set
            - repos (default: "default")  The name of the repo set to use
            - profile (no default, required)  The portage profile to use
            - profile_repo (default: "gentoo")  The repo where the profile is located
            - features (default: ["buildpkg", "binpkg-multi-instance"])
        """
        with open(config_file, "rb") as f:
            config_data = load(f)

        features = DEFAULT_FEATURES.copy()
        if config_features := config_data.get("features", None):
            features.update(set(config_features))

        config_data["name"] = config_data.get("name", "default")
        config_data["repos"] = config_data.get("repos", "default")
        config_data["profile_repo"] = config_data.get("profile_repo", "gentoo")
        config_data["features"] = list(features)

        if "profile" not in config_data:
            raise ValueError(f"Config file missing required value: {c_('profile', 'red', bold=True)}")

        self.config = config_data

    @property
    def pkgdir(self):
        """Returns the directory used for packages for this config name"""
        return Path(f"~/.local/share/BiB/packages/{self.config['name']}").expanduser()

    @property
    def sysroot(self):
        """Returns the system root directory for this config name"""
        return Path(f"~/.local/share/BiB/sysroots/{self.config['name']}").expanduser()

    @property
    def run_root(self):
        """Returns the run root directory for this config name"""
        return Path(f"~/.local/share/BiB/runroots/{self.config['name']}").expanduser()

    @property
    def repo_root(self):
        """Returns the repository root directory for this config name"""
        return Path(f"~/.local/share/BiB/repos/{self.config['name']}").expanduser()

    @property
    def emerge_profiles(self):
        """Lists available emerge profiles"""
        try:
            profiles = run(["eselect", "profile", "list"], capture_output=True, check=True).stdout.decode()
            return profiles
        except SubprocessError as e:
            self.logger.error("Failed to list emerge profiles: %s", e)
            raise e

    def run_emerge(self, args):
        """Runs the emerge command with the passed args"""
        args = ["emerge"] + args

        self.set_portage_profile()  # Ensure the profile is set
        self.set_features()  # Ensure the features are set

        self.logger.info(
            " [E] [%s] %s %s",
            c_(self.config["name"], "green", bright=True, bold=True),
            " ".join(map(str, args)),
        )
        # Open the emerge log, get the current last line so it can be seeked past in the event of build failures
        emerge_log = Path("/var/log/emerge.log")
        emerge_log.touch()
        log_end = emerge_log.stat().st_size
        ret = run(args)

        if ret.returncode:
            self.logger.error("Emerge info:\n" + run(["emerge", "--info"], capture_output=True).stdout.decode())
            with open(emerge_log, "r") as log:
                log.seek(log_end)
                self.logger.error("Emerge log:\n" + log.read())
            raise RuntimeError(f"Failed to run: emerge {args}")

        return ret

    def sync_repos(self):
        """Updates the portage repositories"""
        self.logger.info(" ~*~ [%s] Updating portage repositories", c_(self.config["name"], "blue"))
        self.run_emerge(["--sync"])

    def set_features(self):
        """Sets the portage features in the environment"""
        features_str = " ".join(self.config["features"])
        self.logger.info(
            " ~*~ [%s] Setting portage features: %s", c_(self.config["name"], "blue"), c_(features_str, "green")
        )
        environ["FEATURES"] = features_str

    def init_namespace(self):
        """Initializes the namespace for the current config

        Mounts the system root in an overlayfs mount
        Mounts the system /etc/resolv.conf into the run_root
        Mounts the repository directory into the run_root
        Mounts the package directory into the run_root

        Updates repos after mounts are complete
        """
        self.logger.info("[%s] Initializing namespace", c_(self.config["name"], "blue"))

        self.overlay_mount(self.run_root, self.sysroot, clean=True)
        self.mount_system_dirs()  # Mount system dirs, such as /sys, /proc, /dev
        self.bind_mount("/etc/resolv.conf", self.run_root / "etc/resolv.conf", file=True)
        self.bind_mount(self.repo_root, self.run_root / "var/db/repos", readonly=False)
        self.bind_mount(self.pkgdir, self.run_root / "var/cache/binpkgs", readonly=False)
        self.logger.info(" -/~ Chrooting into: %s", c_(self.run_root, "red"))
        chroot(self.run_root)
        chdir("/")

    def set_portage_profile(self):
        """Sets the portage profile in the chroot"""
        profile_repo = self.config["profile_repo"]
        profile = self.config["profile"]
        profile_sym = Path("/etc/portage/make.profile")
        profile_target = Path(f"/var/db/repos/{profile_repo}/profiles/{profile}")

        if profile_sym.is_symlink() and profile_sym.resolve() == profile_target:
            return self.logger.debug("Portage profile already set: %s -> %s", profile_sym, profile_target)

        self.logger.info(
            " ~-~ [%s] Setting portage profile: %s",
            c_(profile_repo, "yellow"),
            c_(profile, "blue"),
        )

        if profile_sym.exists(follow_symlinks=False):
            profile_sym.unlink()

        if not profile_target.exists():
            self.logger.info(" -+- %s", self.emerge_profiles)
            raise FileNotFoundError(f"Portage profile not found: {profile_target}")

        profile_sym.symlink_to(profile_target, target_is_directory=True)
        self.logger.debug("Set portage profile symlink: %s -> %s", profile_sym, profile_sym.resolve())

    def execute(self, args):
        """Runs a command in the namespace environment"""
        self.init_namespace()
        self.logger.info(" ### Running command: %s", c_(args, "green"))
        run(args)

    def update_seed(self):
        """Updates the seed overlay"""
        self.config.clean_seed = True  # Clean the seed upper/work dirs
        self.config.no_seed_overlay = True  # Don't use an overlay, work on the seed
        self.init_namespace()
        self.logger.info(" >>> Updating seed: %s", c_(self.config.seed_update_args, "green"))
        self.run_emerge(split(self.config.seed_update_args))
        self.run_emerge(["--depclean"])  # Depclean after world update

    def build_package(self, package):
        """Builds a single package based on the current config"""
        self.init_namespace()
        self.logger.info(" +++ Building package: %s", c_(package, "green", bold=True))
        self.run_emerge(
            ["--oneshot", "--autounmask=y", "--autounmask-continue=y", "--usepkg=y", "--jobs=8", "--noreplace", package]
        )
