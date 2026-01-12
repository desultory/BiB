#!/usr/bin/python3

from asyncio import Lock, sleep, to_thread

from aiohttp.web import Application, Response, json_response
from zenlib.logging import loggify
from zenlib.namespace import nsexec
from zenlib.util import colorize as c_

from .emerge_wrapper import EmergeWrapper


class PackageInQueue(Exception):
    pass


@loggify
class BinhostPkgServer:
    """Webserver which takes requests at /pkg?pkg=package_name and builds the package on demand."""

    def __init__(self, config_file="config.toml", listen_ip="127.0.0.1", listen_port=8689, **kwargs):
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.build_queue = []  # Don't tell anyone the queue is a list
        self.queue_lock = Lock()

        self.emerge_wrapper = EmergeWrapper(logger=self.logger, config_file=config_file, **kwargs)

        self.app = Application(logger=self.logger)
        self.app.on_startup.append(self.app_tasks)
        self.app.router.add_get("/pkg", self.add_package)
        self.app.router.add_get("/queue", self.get_queue)
        self.app.router.add_get("/sync", self.handle_sync)
        self.app.router.add_static("/", path=self.emerge_wrapper.pkgdir)

    async def get_queue(self, request):
        return json_response(data=self.build_queue)

    async def enqueue_package(self, package_name):
        async with self.queue_lock:
            if package_name in self.build_queue:
                raise PackageInQueue(f"Package {package_name} is already in the queue.")
            self.build_queue.append(package_name)
            self.logger.info(f"Enqueued package: {c_(package_name, 'green')}")
            return self.build_queue

    async def add_package(self, request):
        package_name = request.query.get("pkg", None)
        if package_name is None:
            return Response(text="No package name provided", status=400)

        try:
            return json_response(data=await self.enqueue_package(package_name))
        except PackageInQueue as e:
            return Response(text=str(e), status=400)

    async def app_tasks(self, app):
        app["build_handler"] = app.loop.create_task(self.handle_queue())

    async def handle_queue(self):
        while True:
            async with self.queue_lock:
                if self.build_queue:
                    package_name = self.build_queue.pop(0)
                else:
                    package_name = None

            if package_name is not None:
                try:
                    ret = await to_thread(nsexec, self.emerge_wrapper.build_package, package_name)
                    if ret is not None:
                        self.logger.warning("Build process returned: %s", ret)
                except RuntimeError as e:
                    self.logger.error("Error building package %s: %s", package_name, e)
            else:
                self.logger.log(5, "Waiting for packages to build")
                await sleep(1)

    async def handle_sync(self, request):
        try:
            ret = await to_thread(nsexec, self.emerge_wrapper.sync_repos)
            if ret is not None:
                self.logger.warning("Sync process returned: %s", ret)
            return Response(text="Sync completed", status=200)
        except RuntimeError as e:
            self.logger.error("Error syncing repositories: %s", e)
            return Response(text=f"Error syncing repositories: {e}", status=500)

    def start(self):
        from aiohttp.web import run_app

        self.logger.debug("Running server on %s:%s", self.listen_ip, self.listen_port)
        run_app(self.app, host=self.listen_ip, port=self.listen_port)

