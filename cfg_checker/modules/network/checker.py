import json
import os
import sys
import ipaddress

from copy import deepcopy

from cfg_checker.reports import reporter
from cfg_checker.common import utils, const
from cfg_checker.common import config, logger, logger_cli, pkg_dir
from cfg_checker.common import salt_utils
from cfg_checker.nodes import SaltNodes, node_tmpl


class NetworkChecker(SaltNodes):
    @staticmethod
    def _map_network_for_host(host, if_class, net_list, data):
        if not any(if_class.ip in net for net in net_list.keys()):
            # IP not fits into existing networks
            if if_class.network not in net_list.keys():
                # create subnet key
                net_list[if_class.network] = {}
            # add the host to the dict
            net_list[if_class.network][host] = data
        else:
            # There is a network that ip fits into
            for _net in net_list.keys():
                if if_class.ip in _net:
                    net_list[_net][host] = data

        return net_list

    def collect_network_info(self):
        """
        Collects info on the network using ifs_data.py script

        :return: none
        """
        logger_cli.info("# Mapping node runtime network data")
        _result = self.execute_script_on_active_nodes("ifs_data.py", args=["json"])

        for key in self.nodes.keys():
            # check if we are to work with this node
            if not self.is_node_available(key):
                continue
            # due to much data to be passed from salt, it is happening in order
            if key in _result:
                _text = _result[key]
                _dict = json.loads(_text[_text.find('{'):])
                self.nodes[key]['routes'] = _dict.pop("routes")
                self.nodes[key]['networks'] = _dict
            else:
                self.nodes[key]['networks'] = {}
                self.nodes[key]['routes'] = {}
            logger_cli.debug("... {} has {} networks".format(
                key,
                len(self.nodes[key]['networks'].keys())
            ))
        logger_cli.info("-> done collecting networks data")

        logger_cli.info("### Building network tree")
        # match interfaces by IP subnets
        _all_nets = {}
        for host, node_data in self.nodes.iteritems():
            if not self.is_node_available(host):
                continue

            for net_name, net_data in node_data['networks'].iteritems():
                # get ips and calculate subnets
                if net_name in ['lo']:
                    # skip the localhost
                    continue
                _ip4s = net_data['ipv4']
                for _ip_str in _ip4s.keys():
                     # create interface class
                    _if = ipaddress.IPv4Interface(_ip_str)
                    net_data['name'] = net_name
                    net_data['if'] = _if

                    _all_nets = self._map_network_for_host(
                        host,
                        _if,
                        _all_nets,
                        net_data
                    )

        # save collected info
        self.all_nets = _all_nets


    def collect_reclass_networks(self):
        logger_cli.info("# Mapping reclass networks")
        # Get networks from reclass and mark them
        _reclass_nets = {}
        # Get required pillars
        self.get_specific_pillar_for_nodes("linux:network")
        for node in self.nodes.keys():
            # check if this node
            if not self.is_node_available(node):
                continue
            # get the reclass value
            _pillar = self.nodes[node]['pillars']['linux']['network']
            _pillar = _pillar['interface']
            for _if_name, _if_data in _pillar.iteritems():
                if 'address' in _if_data:
                    _if = ipaddress.IPv4Interface(
                        _if_data['address'] + '/' + _if_data['netmask']
                    )
                    _if_data['name'] = _if_name
                    _if_data['if'] = _if

                    _reclass_nets = self._map_network_for_host(
                        node,
                        _if,
                        _reclass_nets,
                        _if_data
                    )

        self.reclass_nets = _reclass_nets


    def print_network_report(self):
        """
        Create text report for CLI

        :return: none
        """
        _all_nets = self.all_nets.keys()
        logger_cli.info("# Reclass networks")
        logger_cli.info(
            "    {0:17} {1:25}: {2:19} {3:5}{4:10} {5}{6} {7} / {8} / {9}".format(
                "Hostname",
                "IF",
                "IP",
                "rtMTU",
                "rcMTU",
                "rtState",
                "rcState",
                "rtGate",
                "rtDef.Gate",
                "rcGate"
            )
        )

        _reclass = [n for n in _all_nets if n in self.reclass_nets]
        for network in _reclass:
            # shortcuts
            _net = str(network)
            logger_cli.info("-> {}".format(_net))
            names = sorted(self.all_nets[network].keys())
            for hostname in names:
                if not self.is_node_available(hostname, log=False):
                   logger_cli.info(
                        "    {0:17} {1}".format(
                            hostname.split('.')[0],
                            "... no data for the node"
                        )
                    )

                # get the gateway for current net
                _routes = self.nodes[hostname]['routes']
                _route = _routes[_net] if _net in _routes else None
                if not _route:
                    _gate = "no route!"
                else:
                    _gate = _route['gateway'] if _route['gateway'] else "empty"
                
                # get the default gateway
                if 'default' in _routes:
                    _d_gate = ipaddress.IPv4Address(
                        _routes['default']['gateway']
                    )
                else:
                    _d_gate = None
                _d_gate_str = _d_gate if _d_gate else "No default gateway!"

                _a = self.all_nets[network][hostname]
                _r = self.reclass_nets[network][hostname]
                
                # Take gateway parameter for this IF 
                # from corresponding reclass record
                _pillar = self.nodes[hostname]['pillars']
                _pillar = _pillar['linux']['network']['interface']
                if not self.is_node_available(hostname):
                    _r_gate = "-"
                elif _a['name'] not in _pillar:
                    _r_gate = "no IF in reclass!"
                else:
                    _rd = _pillar[_a['name']]
                    _r_gate = _rd['gateway'] if 'gateway' in _rd else "empty"

                _text = "{0:25}: {1:19} {2:5}{3:10} {4:4}{5:10} {6} / {7} / {8}".format(
                    _a['name'],
                    str(_a['if'].ip),
                    _a['mtu'],
                    '('+str(_r['mtu'])+')' if 'mtu' in _r else '(unset!)',
                    _a['state'],
                    "(enabled)" if _r['enabled'] else "(disabled)",
                    _gate,
                    _d_gate_str,
                    _r_gate
                )
                logger_cli.info(
                    "    {0:17} {1}".format(hostname.split('.')[0], _text)
                )
        
        logger_cli.info("\n# Other networks")
        _other = [n for n in _all_nets if n not in self.reclass_nets]
        for network in _other:
            logger_cli.info("-> {}".format(str(network)))
            names = sorted(self.all_nets[network].keys())

            for hostname in names:
                _text = "{0:25}: {1:19} {2:5} {3:4}".format(
                    self.all_nets[network][hostname]['name'],
                    str(self.all_nets[network][hostname]['if'].ip),
                    self.all_nets[network][hostname]['mtu'],
                    self.all_nets[network][hostname]['state']
                )
                logger_cli.info(
                    "    {0:17} {1}".format(hostname.split('.')[0], _text)
                )

    
    def create_html_report(self, filename):
        """
        Create static html showing network schema-like report

        :return: none
        """
        logger_cli.info("### Generating report to '{}'".format(filename))
        _report = reporter.ReportToFile(
            reporter.HTMLNetworkReport(),
            filename
        )
        _report({
            "nodes": self.nodes,
            "diffs": {}
        })
        logger_cli.info("-> Done")
