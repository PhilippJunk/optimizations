"""Custom classes for hacking pyBNF."""

import copy
import logging

from typing import Callable

import numpy as np
import pybnf
import pybnf.algorithms
import pybnf.cluster
import pybnf.config
import pybnf.parse
import pybnf.pset

logger = logging.getLogger(__name__)


class CustomData(pybnf.data.Data):
    """
    TODO
    """

    @classmethod
    def from_data_and_result(cls, data, result):
        '''
        Create custom Data object from data and result.

        Called by NpModel.execute.
        '''
        nrows_data, ncols_data = data.shape
        ncols_result = result.shape[1]

        out = cls(arr=np.hstack([np.arange(nrows_data).reshape((nrows_data, 1)), data, result]))
        # init header
        colnames = (
            ['time'] + [f'x{i:0{10}d}' for i in range(ncols_data)] + [f'y{i:0{10}d}' for i in range(ncols_result)]
        )
        out.cols = {c: i for i, c in enumerate(colnames)}
        out.headers = {i: c for i, c in enumerate(colnames)}
        # set indvar
        out.indvar = 'time'

        return out

    @classmethod
    def from_x_and_y(cls, x, y):
        '''
        Create custom Data object from x and y.

        Required for creating CustomConfiguration object.

        Parameters
        ----------
        x
          Inputs: 1D or 2D. If 2D, observations x inputs
        y
          Outputs: 1D or 2D. If 2D, observations x outputs
        '''
        x, y = np.asarray(x), np.asarray(y)

        if x.ndim == 1:
            x = np.atleast_2d(x).T
        if y.ndim == 1:
            y = np.atleast_2d(y).T

        if x.shape[0] != y.shape[0]:
            raise ValueError('different number of observations between dependent and independent variables')

        if x.ndim > 2 or y.ndim > 2:
            raise ValueError('wrong dimensionality (>2)')

        # get number of cols
        ncols_x, ncols_y = x.shape[1], y.shape[1]

        # make dummy t variable
        t = np.atleast_2d(np.arange(x.shape[0])).T

        # arrange data frame
        xy = np.hstack([t, x, y])

        # create output
        out = cls(arr=xy)
        # init header
        colnames = ['time'] + [f'x{i:0{10}d}' for i in range(ncols_x)] + [f'y{i:0{10}d}' for i in range(ncols_y)]
        out.cols = {c: i for i, c in enumerate(colnames)}
        out.headers = {i: c for i, c in enumerate(colnames)}
        # set indvar
        out.indvar = 'time'

        return out

    def get_data_arr(self):
        data_indxs = [i for i, col in self.headers.items() if col.startswith('x')]

        return self._data[:, data_indxs]


class NpModel(pybnf.pset.Model):
    def __init__(
        self,
        fun: Callable[[np.ndarray, np.ndarray], np.ndarray],
        data: CustomData,
        n_params: int,
        pset: pybnf.pset.PSet | None = None,
    ):
        self.fun = fun
        self.data = data.get_data_arr()
        self.pset = pset

        self.suffixes = [('simulate', '_data')]
        self.mutants = []
        self.file_path = '_optimization'
        self.name = '_optimization'

        self.param_names = [f'v{i:0{10}d}__FREE' for i in range(n_params)]

    def copy_with_param_set(self, pset: pybnf.pset.PSet):
        new = copy.deepcopy(self)
        new.pset = pset
        return new

    def save(self, file_prefix, **kwargs):
        pass

    def execute(self, folder, filename, timeout):
        params = np.fromstring(self.pset.values_to_string(), sep='\t')
        res = self.fun(self.data, params)
        data = CustomData.from_data_and_result(self.data, res)
        [suffix] = self.get_suffixes()
        return {suffix: data}

    def get_suffixes(self):
        result = []
        for s in self.suffixes:
            result.append(s[1])
            for mut in self.mutants:
                result.append(s[1] + mut.suffix)
        return result


class CustomConfiguration(pybnf.config.Configuration):
    def __init__(self, d=None):
        if d is None:
            d = dict()

        if 'models' not in d or len(d['models']) == 0:
            raise UnspecifiedConfigurationKeyError("'model' must be specified in the configuration file.")
        if 'fit_type' not in d:
            d['fit_type'] = 'de'
            pybnf.printing.print1('Warning: fit_type was not specified. Defaulting to de (Differential Evolution).')
        if d['fit_type'] == 'bmc':
            d['fit_type'] = 'mh'  # 'bmc' option was renamed to 'mh'. Preserve backwards compatibility.
        if 'objfunc' not in d:
            pybnf.printing.print1('Warning: objfunc was not specified. Defaulting to chi_sq.')
        if not self._req_user_params() <= d.keys() and d['fit_type'] != 'check':
            unspecified_keys = []
            for k in self._req_user_params():
                if k not in d.keys():
                    unspecified_keys.append(k)
            raise UnspecifiedConfigurationKeyError(
                "The following configuration keys must be specified:\n\t" + ",".join(unspecified_keys)
            )

        if d['fit_type'] == 'check':
            d = self.check_unused_keys_model_checking(d)
        elif pybnf.printing.verbosity >= 1:
            self.check_unused_keys(d)
            pass
        if d['fit_type'] in ('mh', 'pt', 'sa', 'dream', 'am'):
            self.postprocess_mcmc_keys(d)
        self.config = self.default_config()
        for k, v in d.items():
            self.config[k] = v

        self._data_map = dict()  # Internal structure to help get both regular and mutant data to the right place

        logger.debug('Loaded model:exp mapping')
        self.exp_data, self.constraints = self._load_exp_data()

        self.models = self._load_models()
        logger.debug('Loaded models')

        self._load_actions()
        logger.debug('Loaded actions')

        self._load_simulators()
        logger.debug('Loaded simulators')

        self._load_mutants()
        logger.debug('Loaded mutants')

        self.mapping = self._check_actions()  # dict of model prefix -> set of experimental data prefixes
        logger.debug('Loaded data')

        self.obj = self._load_obj_func()
        logger.debug('Loaded objective function')

        self.variables = self._load_variables()
        self._check_variable_correspondence()
        logger.debug('Loaded variables')

        self._postprocess_normalization()
        self._load_postprocessing()
        self.config['time_length'] = self._load_t_length()
        logger.debug('Completed configuration')

    def _load_models(self):
        if 'numpy_model' not in self.config.keys():
            return super()._load_models()

        return {
            '_optimization': NpModel(
                self.config['_custom_func'],
                self.exp_data['_optimization']['_data'],
                3,
                None,
            )
        }

    def _load_exp_data(self):
        if 'numpy_model' not in self.config.keys():
            return super()._load_exp_data()

        self._data_map = {
            '_optimization': [
                '_data',
            ]
        }
        return {'_optimization': {'_data': self.config['_custom_data']}}, set()