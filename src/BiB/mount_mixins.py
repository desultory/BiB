from pathlib import Path
from shutil import rmtree
from subprocess import run

from zenlib.util import colorize as c_


class MountMixins:
    def tmpfs_mount(self, mountpoint: Path, size: int = 0, mode: str = "rw"):
        """Creates a tmpfs mount at the specified mountpoint with the specified size and mode.
        If size is 0, the size is unlimited.
        """
        mountpoint = Path(mountpoint)
        if not mountpoint.exists():
            self.logger.debug("[tmpfs] Creating mountpoint: %s", mountpoint)
            mountpoint.mkdir(parents=True)

        args = ["mount", "-t", "tmpfs", "tmpfs", mountpoint]
        if size:
            args.extend(["-o", f"size={size}"])
        if mode:
            args.extend(["-o", mode])

        self.logger.info(" +/~ Mounting tmpfs on: %s", c_(mountpoint, "yellow"))
        run(args, check=True)

    def overlay_mount(
        self,
        mountpoint: Path,
        lower: Path,
        work: Path = None,
        upper: Path = None,
        userxattr=True,
        temp=False,
        clean=False,
        log=True,
    ):
        """Mounts an overlayfs using the specified lower dir and mountpint.
        If an upper or work directory is not specified, they will be created in the same directory as the lower dir
        with the names .<lower_name>_upper and .<lower_name>_work respectively.

        If temp is set, creates .<lower_name>_temp, mounts a tmpfs over it, then uses it for the upper and work dirs.
        if clean is set, the upper and work directories will be cleared before mounting.
        """
        mountpoint, lowerdir = Path(mountpoint), Path(lower)
        if not lowerdir.exists():
            raise FileNotFoundError(f"Lower directory not found: {lowerdir}")
        if not mountpoint.exists():
            self.logger.debug("[overlay] Creating mountpoint: %s", mountpoint)
            mountpoint.mkdir(parents=True)
        elif mountpoint.is_mount():
            self.logger.info(" - - Unmounting overlay on: %s", mountpoint)
            run(["umount", mountpoint], check=True)

        if temp:
            tmpdir = lowerdir.with_name(f".{lowerdir.name}_temp")
            self.tmpfs_mount(tmpdir)
            upper = tmpdir / "upper"
            work = tmpdir / "work"
        else:
            upper = Path(upper) if upper else lowerdir.with_name(f".{lowerdir.name}_upper")
            work = Path(work) if work else upper.with_name(f".{lowerdir.name}_work")

        if clean:
            for d in [upper, work]:
                if d.exists():
                    self.logger.warning(" --- Cleaning directory: %s", (c_(d, "yellow")))
                    rmtree(d)

        if not upper.exists():
            self.logger.debug("[overlay] Creating upper directory: %s", upper)
            upper.mkdir(parents=True)
        if not work.exists():
            self.logger.debug("[overlay] Creating work directory: %s", work)
            work.mkdir(parents=True)

        options = "userxattr," if userxattr else ""
        options += f"lowerdir={lowerdir},upperdir={upper},workdir={work}"
        args = ["mount", "-t", "overlay", "overlay", "-o", options, str(mountpoint)]
        self.logger.debug("[overlay] Using options: %s", options)
        loglevel = 20 if log else 10
        self.logger.log(loglevel, " ~/* Mounting overlay on: %s", c_(mountpoint, "cyan", bold=True))
        run(args, check=True)

    def bind_mount(self, source: Path, dest: Path, recursive=False, readonly=True, file=False):
        """Bind mounts a source directory over a destination directory"""
        source, dest = Path(source), Path(dest)
        if recursive:
            mount_type = "--rbind"
            s1 = "*"
        else:
            mount_type = "--bind"
            s1 = "+"
        if file:
            s1 = "."

        s2 = ">" if readonly else "-"

        if dest.is_mount():
            self.logger.info(" - - Unmounting %s: %s", c_(source, "red"), c_(dest, "magenta"))
            run(["umount", dest], check=True)

        if not source.exists():
            if file:
                self.logger.debug("Creating mount source file: %s", source)
                source.touch()
            else:
                self.logger.debug("Creating mount source directory: %s", source)
                source.mkdir(parents=True)

        if not dest.exists():
            if file:
                self.logger.debug("Creating mount destination file: %s", dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.touch()
            else:
                self.logger.debug("Creating mount destination directory: %s", dest)
                dest.mkdir(parents=True)

        args = ["mount", mount_type, source, dest]
        if readonly:
            args.extend(["-o", "ro"])

        self.logger.info(
            " %s%s%s Mounting %s over: %s", s1, s2, s1, c_(source, "green"), c_(dest, "magenta", bold=True)
        )
        run(args, check=True)

    def mount_system_dirs(self):
        """Mounts /proc, /sys, and /dev in the run_root directory."""
        self.logger.info(" *v* Mounting system directories in: %s", c_(self.run_root, "cyan", bold=True))
        self.bind_mount("/proc", self.run_root / "proc", recursive=True)
        self.bind_mount("/sys", self.run_root / "sys", recursive=True)
        self.bind_mount("/dev", self.run_root / "dev", recursive=True)
        self.bind_mount("/run", self.run_root / "run", recursive=True)
        run(["mount", "--types", "devpts", "devpts", self.run_root / "dev/pts"], check=True)
