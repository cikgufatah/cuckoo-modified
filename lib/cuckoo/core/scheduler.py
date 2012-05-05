import os
import sys
import time
import shutil
import logging
from threading import Thread

from lib.cuckoo.abstract.dictionary import Dictionary
from lib.cuckoo.abstract.machinemanager import MachineManager
from lib.cuckoo.common.utils import create_folders, get_file_md5, get_file_type
from lib.cuckoo.common.config import Config
from lib.cuckoo.common.database import Database
from lib.cuckoo.core.guest import GuestManager
from lib.cuckoo.core.packages import choose_package

log = logging.getLogger(__name__)

MMANAGER = None

class AnalysisManager(Thread):
    def __init__(self, task):
        Thread.__init__(self)
        self.task = task
        self.cfg = Config()
        self.db = Database()
        self.analysis = Dictionary()

    def init_storage(self):
        self.analysis.results_folder = os.path.join(os.path.join(os.getcwd(), "storage/analyses/"), str(self.task.id))

        if os.path.exists(self.analysis.results_folder):
            log.error("Analysis results folder already exists at path \"%s\", analysis aborted" % self.analysis.results_folder)
            return False

        try:
            os.mkdir(self.analysis.results_folder)
        except OSError as e:
            log.error("Unable to create analysis results \"%s\" folder: %s, analysis aborted" % (self.analysis.results_folder, e))
            return False
        
        return True

    def store_file(self):
        md5 = get_file_md5(self.task.file_path)
        self.analysis.stored_file_path = os.path.join(os.path.join(os.getcwd(), "storage/binaries/"), md5)

        if os.path.exists(self.analysis.stored_file_path):
            log.info("File already exists at \"%s\"" % self.analysis.stored_file_path)
        else:
            try:
                shutil.copy(self.task.file_path, self.analysis.stored_file_path)
            except shutil.error as e:
                log.error("Unable to store file from \"%s\" to \"%s\": %s"
                          % (self.task.file_path, self.analysis.stored_file_path, e))
                return False

        try:
            os.symlink(self.analysis.stored_file_path, os.path.join(self.analysis.results_folder, "binary"))
        except OSError as e:
            return False

        return True

    def build_options(self):
        options = {}
        self.analysis.file_type = get_file_type(self.task.file_path)
        
        if not self.task.package:
            package = choose_package(self.analysis.file_type)
            if not package:
                log.error("No default package supports the file format \"%s\", analysis aborted" % self.analysis.file_type)
                return False
        else:
            package = self.task.package

        if not self.task.timeout:
            timeout = self.cfg.analysis_timeout
        else:
            timeout = self.task.timeout

        options["file_path"] = self.task.file_path
        options["file_name"] = os.path.basename(self.task.file_path)
        options["package"] = package
        options["timeout"] = timeout

        return options

    def run(self):
        if not os.path.exists(self.task.file_path):
            log.error("The file to analyze does not exist at path \"%s\", analysis aborted" % self.task.file_path)
            return False

        if not self.init_storage():
            return False

        self.store_file()

        options = self.build_options()
        if not options:
            return False

        while True:
            vm = MMANAGER.acquire(label=self.task.machine, platform=self.task.platform)
            if not vm:
                time.sleep(1)
            else:
                break

        #MMANAGER.start(vm.label)
        guest = GuestManager(vm.ip, vm.platform)
        guest.start_analysis(options)
        guest.wait_for_completion()
        guest.save_results(self.analysis.results_folder)
        #MMANAGER.stop(vm.label)

class Scheduler:
    def __init__(self):
        self.running = True
        self.config = Config()
        self.db = Database()

    def initialize(self):
        global MMANAGER

        name = "plugins.machinemanagers.%s" % self.config.machine_manager
        try:
            __import__(name, globals(), locals(), ["dummy"], -1)
        except ImportError as e:
            sys.exit("Unable to import machine manager plugin: %s" % e)

        MachineManager()
        module = MachineManager.__subclasses__()[0]
        MMANAGER = module()
        MMANAGER.initialize()

        if len(MMANAGER.machines) == 0:
            sys.exit("No machines available")
        else:
            log.info("Loaded %s machine/s" % len(MMANAGER.machines))

    def stop(self):
        self.running = False

    def start(self):
        self.initialize()

        while self.running:
            time.sleep(1)
            task = self.db.fetch()

            if not task:
                log.debug("No pending tasks")
                continue

            analysis = AnalysisManager(task)
            analysis.daemon = True
            analysis.start()
            analysis.join()
            break