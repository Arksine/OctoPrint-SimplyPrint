"""
This script should be run in the background on the Raspberry Pi, to check that OctoPrint is alive and if it is not alive
then it will let SP know that it has died, and await instruction on how to proceed.
"""
import logging
import threading
import time

import requests

from octoprint.settings import settings
from octoprint.util import ResettableTimer
from octoprint.util.commandline import CommandlineCaller

from octoprint_simplyprint.comm.constants import UPDATE_URL, API_VERSION
from octoprint_simplyprint.local.util import OctoPrintClient, OctoPrintApiError


def run_background_check():
    simply_background = SimplyPrintBackground()
    simply_background.mainloop()


class SimplyPrintBackground:
    def __init__(self):
        self._logger = logging.getLogger()
        self._logger.setLevel(logging.DEBUG)

        try:
            self._octoprint_settings = settings(init=True)
            # We need init as this runs in a separate process
            # NOTE: This should not be used to write to the settings file, since it could cause a conflict
            # with OctoPrint's settings, and lose some settings there.
        except ValueError:
            self._logger.error("This script shouldn't be run in the same process as OctoPrint")
            self._logger.error("So don't do that :) ")
            return

        self.octoprint = None

        self.main_thread = None
        self.run = True

        self.safe_mode_checks = 0

    def mainloop(self):
        # TODO find out the port somehow... that's going to annoy me
        self.octoprint = OctoPrintClient("http://127.0.0.1", self._octoprint_settings.get(["api", "key"]))

        while self.run:
            try:
                start = time.time()

                check_result = self.check_octoprint()
                if not check_result:
                    # :(
                    self._logger.warning("OctoPrint is not OK... Trying to restart it now")
                else:
                    self._logger.debug("OctoPrint seems OK")

                    safe_mode = self.check_safemode()
                    if not safe_mode:
                        self._logger.warning("OctoPrint is in safe mode")
                        if self.safe_mode_checks == 0 or self.safe_mode_checks > 10:
                            # Restart immediately, or after more than 10 mins
                            self.restart_octoprint()
                            self.safe_mode_checks = 0

                    else:
                        self._logger.debug("OctoPrint is not in safe mode")

                    self.safe_mode_checks += 1

                total_time = time.time() - start
                self._logger.debug("OctoPrint health check took {}".format(total_time))
                if self.run:
                    time.sleep(5 - total_time)
            except Exception as e:
                self._logger.exception(e)
                time.sleep(60)

    def check_octoprint(self):
        """
        Checks OctoPrint is alive
        """
        try:
            version = self.octoprint.version()
        except OctoPrintApiError:
            return False

        if "octoprint" in version["text"].lower():
            return True
        else:
            return False

    def check_safemode(self):
        def check_server():
            try:
                server = self.octoprint.server()
            except OctoPrintApiError:
                # OctoPrint < 1.5.0, no /api/server
                return False

            return server["safemode"] is not None

        def check_pgmr():
            try:
                pgmr = self.octoprint.plugin_plugin_manager()
            except OctoPrintApiError:
                # Now it's possible its dead, more likely that user disabled plugin manager :/
                # Return True since its not definitive
                return True

            for plugin in pgmr["plugins"]:
                if plugin["safe_mode_victim"]:
                    return False

            return True

        if not check_server():
            return check_pgmr()
        else:
            return True

    def restart_octoprint(self):
        command = self._octoprint_settings.get(["server", "commands", "serverRestartCommand"])
        if not command:
            self._logger.warning("No command configured, can't restart")
            return

        caller = CommandlineCaller()
        try:
            code, stdout, stderr = caller.call(command, **{"shell": True}) # Use shell=True, as we have to trust user input
        except Exception as e:
            self._logger.error("Error calling command to restart server {}".format(command))
            self._logger.exception(e)
            return

        if code != 0:
            self._logger.error("Non zero return code running '{}' to restart server: {}".format(command, code))
            self._logger.exception("STDOUT: {}".format(stdout))
            self._logger.exception("STDERR: {}".format(stderr))

    def ping_simplyprint(self, parameters):
        rpi_id = self._octoprint_settings.get(["plugins", "SimplyPrint", "rpi_id"])
        if not rpi_id:
            # Not set up properly, nothing we can do - let the plugin handle getting a new ID
            return

        url = UPDATE_URL + "?id=" + rpi_id + "&api_version=" + API_VERSION
        url = url.replace(" ", "%20")

        try:
            response = requests.get(url)
        except requests.exceptions.RequestException as e:
            self._logger.error("Error sending get request to SimplyPrint")
            self._logger.exception(e)
            raise

        return response


if __name__ == '__main__':
    run_background_check()
