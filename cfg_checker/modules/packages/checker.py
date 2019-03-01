import json
import os
#import sys

from copy import deepcopy

from cfg_checker import reporter
from cfg_checker.common import utils, const
from cfg_checker.common import config, logger, logger_cli, pkg_dir
from cfg_checker.common import salt_utils
from cfg_checker.nodes import SaltNodes, node_tmpl


class CloudPackageChecker(SaltNodes):
    def collect_installed_packages(self):
        """
        Collect installed packages on each node
        sets 'installed' dict property in the class

        :return: none
        """
        logger_cli.info("### Collecting installed packages")
        _result = self.execute_script("pkg_versions.py")

        for key in self.nodes.keys():
            # due to much data to be passed from salt, it is happening in order
            if key in _result:
                _text = _result[key]
                _dict = json.loads(_text[_text.find('{'):])
                self.nodes[key]['packages'] = _dict
            else:
                self.nodes[key]['packages'] = {}
            logger_cli.debug("# {} has {} packages installed".format(
                key,
                len(self.nodes[key]['packages'].keys())
            ))
        logger_cli.info("-> Done")

    def collect_packages(self):
        """
        Check package versions in repos vs installed

        :return: no return values, all date put to dict in place
        """
        _all_packages = {}
        for node_name, node_value in self.nodes.iteritems():
            for package_name in node_value['packages']:
                if package_name not in _all_packages:
                    _all_packages[package_name] = {}
                _all_packages[package_name][node_name] = node_value

        # TODO: process data for per-package basis

        self.all_packages = _all_packages

    def create_html_report(self, filename):
        """
        Create static html showing packages diff per node

        :return: buff with html
        """
        logger_cli.info("### Generating report to '{}'".format(filename))
        _report = reporter.ReportToFile(
            reporter.HTMLPackageCandidates(),
            filename
        )
        _report({
            "nodes": self.nodes,
            "diffs": {}
        })
        logger_cli.info("-> Done")