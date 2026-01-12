#!/usr/bin/python3

from zenlib.util import get_kwargs

from .package_server import BinhostPkgServer as BiB


def main():
    arguments = [
            {"flags": ["config_file"], "help": "Path to config file", "action": "store", "default": "config.toml", "nargs": "?"},
    ]

    kwargs = get_kwargs(package=__package__, description="Builds packages on demand", arguments=arguments, strict=True)

    bib = BiB(**kwargs)
    bib.start()


if __name__ == "__main__":
    main()
