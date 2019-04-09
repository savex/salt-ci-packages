import jinja2
import six
import abc
import os

from cfg_checker.common import const
from cfg_checker.common import logger, logger_cli
from cfg_checker.helpers.console_utils import Progress

pkg_dir = os.path.dirname(__file__)
pkg_dir = os.path.join(pkg_dir, os.pardir, os.pardir)
pkg_dir = os.path.normpath(pkg_dir)


def line_breaks(text):
    # replace python linebreaks with html breaks
    return text.replace("\n", "<br />")


def get_sorted_keys(td):
    # detect if we can sort by desc
    # Yes, this is slow, but bullet-proof from empty desc
    _desc = all([bool(td[k]['desc']) for k in td.keys()])
    # Get sorted list
    if not _desc:
        return sorted(td.keys())
    else:
        return sorted(
            td.keys(),
            key=lambda k: (
                td[k]['desc']['component'],
                td[k]['desc']['app'],
                k
            )
        )


def get_max(_list):
    return sorted(_list)[-1]



def make_action_label(act):
    return const.all_actions[act]


def make_status_label(sts):
    return const.all_statuses[sts]


@six.add_metaclass(abc.ABCMeta)
class _Base(object):
    def __init__(self):
        self.jinja2_env = self.init_jinja2_env()

    @abc.abstractmethod
    def __call__(self, payload):
        pass

    @staticmethod
    def init_jinja2_env():
        return jinja2.Environment(
            loader=jinja2.FileSystemLoader(os.path.join(pkg_dir, 'templates')),
            trim_blocks=True,
            lstrip_blocks=True)


class _TMPLBase(_Base):
    @abc.abstractproperty
    def tmpl(self):
        pass

    @staticmethod
    def _count_totals(data):
        data['counters']['total_nodes'] = len(data['nodes'])

    def __call__(self, payload):
        # init data structures
        data = self.common_data()
        # payload should have pre-sorted structure
        # system, nodes, clusters, and the rest in other
        data.update({
            "nodes": payload['nodes'],
            "rc_diffs": payload['rc_diffs'],
            "all": payload['all_pkg'],
            "mcp_release": payload['mcp_release'],
            "openstack_release": payload['openstack_release'],
            "tabs": {}
        })

        # add template specific data
        self._extend_data(data)

        # do counts global
        self._count_totals(data)

        # specific filters
        self.jinja2_env.filters['linebreaks'] = line_breaks
        self.jinja2_env.filters['make_status_label'] = make_status_label
        self.jinja2_env.filters['make_action_label'] = make_action_label
        self.jinja2_env.filters['get_max'] = get_max
        self.jinja2_env.filters['get_sorted_keys'] = get_sorted_keys

        # render!
        logger_cli.info("-> Using template: {}".format(self.tmpl))
        tmpl = self.jinja2_env.get_template(self.tmpl)
        logger_cli.info("-> Rendering")
        return tmpl.render(data)

    def common_data(self):
        return {
            'counters': {},
            'salt_info': {}
        }

    def _extend_data(self, data):
        pass


# HTML Package versions report
class CSVAllPackages(_TMPLBase):
    tmpl = "pkg_versions_csv.j2"


# HTML Package versions report
class HTMLPackageCandidates(_TMPLBase):
    tmpl = "pkg_versions_html.j2"

    def _extend_data(self, data):
        logger_cli.info("-> Sorting packages")
        # labels
        data['status_err'] = const.VERSION_ERR
        data['status_down'] = const.VERSION_DOWN

        # Presort packages
        data['critical'] = {}
        data['system'] = {}
        data['other'] = {}
        data['unlisted'] = {}

        _l = len(data['all'])
        _progress = Progress(_l)
        _progress_index = 0
        # counters
        _ec = _es = _eo = _eu = 0
        _dc = _ds = _do = _du = 0
        while _progress_index < _l:
            # progress bar
            _progress_index += 1
            _progress.write_progress(_progress_index)
            # sort packages
            _pn, _val = data['all'].popitem()
            if not _val['desc']:
                # not listed package in version lib
                data['unlisted'].update({
                    _pn: _val
                })
                _eu += _val['results'].keys().count(const.VERSION_ERR)
                _du += _val['results'].keys().count(const.VERSION_DOWN)
            else:
                _c = _val['desc']['component']
                # critical: not blank and not system
                if len(_c) > 0 and _c != 'System':
                    data['critical'].update({
                        _pn: _val
                    })
                    _ec += _val['results'].keys().count(const.VERSION_ERR)
                    _dc += _val['results'].keys().count(const.VERSION_DOWN)
                # system
                elif _c == 'System':
                    data['system'].update({
                        _pn: _val
                    })
                    _es += _val['results'].keys().count(const.VERSION_ERR)
                    _ds += _val['results'].keys().count(const.VERSION_DOWN)
                # rest
                else:
                    data['other'].update({
                        _pn: _val
                    })
                    _eo += _val['results'].keys().count(const.VERSION_ERR)
                    _do += _val['results'].keys().count(const.VERSION_DOWN)

        
        _progress.newline()

        data['errors'] = {
            'mirantis': _ec,
            'system': _es,
            'other': _eo,
            'unlisted': _eu
        }
        data['downgrades'] = {
            'mirantis': _dc,
            'system': _ds,
            'other': _do,
            'unlisted': _du
        }


# Package versions report
class HTMLModelCompare(_TMPLBase):
    tmpl = "model_tree_cmp_tmpl.j2"

    def _extend_data(self, data):
        # move names into separate place
        data["names"] = data["rc_diffs"].pop("diff_names")
        data["tabs"] = data.pop("rc_diffs")
        
        # counters - mdl_diff
        for _tab in data["tabs"].keys():
            data['counters'][_tab] = len(data["tabs"][_tab]["diffs"].keys())


class HTMLNetworkReport(_TMPLBase):
    tmpl = "network_check_tmpl.j2"


class ReportToFile(object):
    def __init__(self, report, target):
        self.report = report
        self.target = target

    def __call__(self, payload):
        payload = self.report(payload)

        if isinstance(self.target, six.string_types):
            self._wrapped_dump(payload)
        else:
            self._dump(payload, self.target)

    def _wrapped_dump(self, payload):
        with open(self.target, 'wt') as target:
            self._dump(payload, target)

    @staticmethod
    def _dump(payload, target):
        target.write(payload)
