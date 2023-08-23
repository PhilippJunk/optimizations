"""Module providing a usable interface to run optimizations."""

import os
import shutil

from typing import Callable, List, Tuple

import numpy as np
import numpy.typing as npt
import pandas as pd
import pybnf
import pybnf.cluster
import pybnf.algorithms
import pydantic
import scipy

from .custom_classes import CustomData, CustomConfiguration


class UniformParam(pydantic.BaseModel):
    var_type: str
    lower_bound: pydantic.FiniteFloat
    upper_bound: pydantic.FiniteFloat

    @pydantic.field_validator("var_type")
    def validate_var_types(cls, var_type):
        valid = ["uniform_var", "loguniform_var"]
        if var_type not in valid:
            raise ValueError(f"var_type can only contain {valid}, found {var_type}")
        return var_type

    @pydantic.model_validator(mode="after")
    def validate_bounds(self):
        assert (
            self.lower_bound < self.upper_bound
        ), f"Lower bound should be less than upper bound"
        return self

    def to_config_key_value_pair(
        self, i: int
    ) -> Tuple[Tuple[str, str], Tuple[float, float, bool]]:
        return (self.var_type, f"v{i:0{10}d}__FREE"), (
            self.lower_bound,
            self.upper_bound,
            True,
        )


class ParamConfig(pydantic.BaseModel):
    params: List[UniformParam]

    def update_param_dict(self, d):
        for i, p in enumerate(self.params):
            key, value = p.to_config_key_value_pair(i)
            d[key] = value
        d["n_params"] = len(self.params)
        return d


def all_equal_bounds(
    n_params: int, var_type: str, lower_bound: float, upper_bound: float
) -> ParamConfig:
    params = [
        UniformParam(
            var_type=var_type, lower_bound=lower_bound, upper_bound=upper_bound
        )
        for _ in range(n_params)
    ]
    return ParamConfig(params=params)


class AlgConfig_DifferentialEvolution(pydantic.BaseModel):
    fit_type: str = pydantic.Field(default="de", init_var=False)
    mutation_rate: float = 0.5
    mutation_factor: float = 1.0
    stop_tolerance: float = 0.002
    de_strategy: str = "rand1"
    islands: int = 1
    migrate_every: int = 20
    num_to_migrate: int = 5

    @pydantic.field_validator("de_strategy")
    def validate_de_strategy(cls, de_strategy):
        valid = ["rand1", "rand2", "best1", "best2", "all1", "all2"]
        if de_strategy not in valid:
            raise ValueError(f"objfunc must be one of {valid}")
        return de_strategy

    def update_param_dict(self, d) -> dict:
        """
        Update parameter dict with settings.
        """
        d.update(self.model_dump())
        return d


class AlgConfig_AsynchronousDifferentialEvolution(pydantic.BaseModel):
    fit_type: str = pydantic.Field(default="ade", init_var=False)
    mutation_rate: pydantic.confloat(ge=0.0, le=1.0) = 0.5
    mutation_factor: pydantic.confloat(ge=0.0, le=1.0) = 1.0
    stop_tolerance: pydantic.confloat(ge=0.0, le=1.0) = 0.002
    de_strategy: str = "rand1"

    @pydantic.field_validator("de_strategy")
    def validate_de_strategy(cls, de_strategy):
        valid = ["rand1", "rand2", "best1", "best2", "all1", "all2"]
        if de_strategy not in valid:
            raise ValueError(f"objfunc must be one of {valid}")
        return de_strategy

    def update_param_dict(self, d) -> dict:
        """
        Update parameter dict with settings.
        """
        d.update(self.model_dump())
        return d


class AlgConfig_ScatterSearch(pydantic.BaseModel):
    fit_type: str = pydantic.Field(default="ss", init_var=False)
    init_size: pydantic.NonNegativeInt | None = None
    local_min_limit: pydantic.NonNegativeInt = 5
    reserve_size: pydantic.NonNegativeInt | None = None

    def update_param_dict(self, d) -> dict:
        """
        Update parameter dict with settings.

        Assumes that the dict already has the general settings incorporated.
        """
        d.update(self.model_dump())

        # handle defaults
        if d["init_size"] is None:
            d["init_size"] = 10 * d["n_params"]
        if d["reserve_size"] is None:
            d["reserve_size"] = d["max_iterations"]

        return d


class AlgConfig_ParticleSwarm(pydantic.BaseModel):
    fit_type: str = pydantic.Field(default="pso", init_var=False)
    cognitive: pydantic.NonNegativeFloat = 1.5
    social: pydantic.NonNegativeFloat = 1.5
    particle_weight: pydantic.NonNegativeFloat = 0.7
    v_stop: pydantic.NonNegativeFloat = 0.0
    particle_weight_final: None = pydantic.Field(default=None, init_var=False)
    adaptive_n_max: pydantic.PositiveInt = pydantic.Field(default=30, init_var=False)
    adaptive_n_stop: pydantic.PositiveInt | pydantic.PositiveFloat = pydantic.Field(
        default=np.inf, init_var=False
    )
    adaptive_abs_tol: pydantic.NonNegativeFloat = pydantic.Field(
        default=0.0, init_var=False
    )
    adaptive_rel_tol: pydantic.NonNegativeFloat = pydantic.Field(
        default=0.0, init_var=False
    )

    @pydantic.field_validator("adaptive_n_stop")
    def validate_adaptive_n_stop(cls, adaptive_n_stop):
        if adaptive_n_stop != np.inf:
            return int(adaptive_n_stop)

    def update_param_dict(self, d) -> dict:
        """
        Update parameter dict with settings.
        """
        d.update(self.model_dump())

        # set particle_weight_final to particle weight to disable adaptive particle swarm
        d["particle_weight_final"] = d["particle_weight"]

        return d


class AlgConfig_AdaptiveParticleSwarm(pydantic.BaseModel):
    fit_type: str = pydantic.Field(default="pso", init_var=False)
    cognitive: pydantic.NonNegativeFloat = 1.5
    social: pydantic.NonNegativeFloat = 1.5
    particle_weight: pydantic.NonNegativeFloat = 0.7
    v_stop: pydantic.NonNegativeFloat = 0.0
    particle_weight_final: pydantic.NonNegativeFloat = 0.5
    adaptive_n_max: pydantic.PositiveInt = 30
    adaptive_n_stop: pydantic.PositiveInt | pydantic.PositiveFloat = np.inf
    adaptive_abs_tol: pydantic.NonNegativeFloat = 0.0
    adaptive_rel_tol: pydantic.NonNegativeFloat = 0.0

    @pydantic.field_validator("adaptive_n_stop")
    def validate_adaptive_n_stop(cls, adaptive_n_stop):
        if adaptive_n_stop != np.inf:
            return int(adaptive_n_stop)

    @pydantic.field_validator("particle_weight_final")
    def validate_particle_weight_final(cls, particle_weight_final, values):
        particle_weight = values.data["particle_weight"]
        assert (
            particle_weight > particle_weight_final
        ), "particle_weight_final has to be less than particle_weight for adaptive particle swarm."
        return particle_weight_final

    def update_param_dict(self, d) -> dict:
        """
        Update parameter dict with settings.
        """
        d.update(self.model_dump())

        return d


class AlgConfig_MetropolisHastingsMCMC(pydantic.BaseModel):
    fit_type: str = pydantic.Field(default="mh", init_var=False)
    step_size: pydantic.PositiveFloat = 0.2
    beta: pydantic.PositiveFloat | List[pydantic.PositiveFloat] = [
        1.0,
    ]
    sample_every: pydantic.PositiveInt = 100
    burn_in: pydantic.NonNegativeInt = 10_000
    output_hist_every: pydantic.PositiveInt = 100
    hist_bins: pydantic.PositiveInt = 10
    credible_intervals: List[pydantic.conint(gt=0, lt=100)] = [68, 95]

    @pydantic.field_validator("beta")
    def validate_beta(cls, beta):
        if isinstance(beta, float):
            return [
                beta,
            ]
        return beta

    def update_param_dict(self, d) -> dict:
        """
        Update parameter dict with settings.
        """
        d.update(self.model_dump())

        return d


class AlgConfig_ParallelTempering(pydantic.BaseModel):
    """
    Configuration for Parallel Tempering Optimization.

    If parameter `beta_range` is given, all values in `beta` are ignored.
    """

    fit_type: str = pydantic.Field(default="pt", init_var=False)
    step_size: pydantic.PositiveFloat = 0.2
    beta: pydantic.PositiveFloat | List[pydantic.PositiveFloat] = [
        1.0,
    ]
    sample_every: pydantic.PositiveInt = 100
    burn_in: pydantic.NonNegativeInt = 10_000
    output_hist_every: pydantic.PositiveInt = 100
    hist_bins: pydantic.PositiveInt = 10
    credible_intervals: List[pydantic.conint(gt=0, lt=100)] = [68, 95]
    exchange_every: pydantic.PositiveInt = 20
    reps_per_beta: pydantic.PositiveInt = 1
    beta_range: Tuple[pydantic.PositiveFloat, pydantic.PositiveFloat] | None = None

    @pydantic.field_validator("beta")
    def validate_beta(cls, beta):
        if isinstance(beta, float):
            return [
                beta,
            ]
        return beta

    @pydantic.model_validator(mode="after")
    def validate_betas(self):
        if self.beta_range is not None:
            self.beta = None
        return self

    def update_param_dict(self, d) -> dict:
        """
        Update parameter dict with settings.
        """
        d.update(self.model_dump())

        if self.beta_range is not None:
            del d["beta"]
        else:
            del d["beta_range"]

        return d


class AlgConfig_SimulatedAnnealing(pydantic.BaseModel):
    fit_type: str = pydantic.Field(default="sa", init_var=False)
    step_size: pydantic.PositiveFloat = 0.2
    beta: pydantic.PositiveFloat | List[pydantic.PositiveFloat] = [
        1.0,
    ]
    beta_max: pydantic.PositiveFloat = np.inf
    cooling: pydantic.PositiveFloat = 0.01

    @pydantic.field_validator("beta")
    def validate_beta(cls, beta):
        if isinstance(beta, float):
            return [
                beta,
            ]
        return beta

    def update_param_dict(self, d) -> dict:
        """
        Update parameter dict with settings.
        """
        d.update(self.model_dump())

        return d


class AlgConfig_AdaptiveMCMC(pydantic.BaseModel):
    fit_type: str = pydantic.Field(default="am", init_var=False)
    step_size: pydantic.PositiveFloat = 0.2
    beta: pydantic.PositiveFloat | List[pydantic.PositiveFloat] = [
        1.0,
    ]
    sample_every: pydantic.PositiveInt = 100
    burn_in: pydantic.NonNegativeInt = 10_000
    output_hist_every: pydantic.PositiveInt = 100
    hist_bins: pydantic.PositiveInt = 10
    stabilizingCov: pydantic.PositiveFloat = 0.001
    adaptive: pydantic.PositiveInt = 10_000
    # TODO there are a few more of these parameters but probably not too important

    @pydantic.field_validator("beta")
    def validate_beta(cls, beta):
        if isinstance(beta, float):
            return [
                beta,
            ]
        return beta

    def update_param_dict(self, d) -> dict:
        """
        Update parameter dict with settings.
        """
        d.update(self.model_dump())

        return d


class GeneralConfig(pydantic.BaseModel):
    param_config: ParamConfig
    algorithm_config: AlgConfig_DifferentialEvolution | AlgConfig_AsynchronousDifferentialEvolution | AlgConfig_ScatterSearch | AlgConfig_ParticleSwarm | AlgConfig_AdaptiveParticleSwarm | AlgConfig_MetropolisHastingsMCMC | AlgConfig_ParallelTempering | AlgConfig_SimulatedAnnealing | AlgConfig_AdaptiveMCMC
    objfunc: str = "sos"
    population_size: pydantic.PositiveInt
    max_iterations: pydantic.PositiveInt
    verbosity: pydantic.conint(ge=0, le=2)

    @pydantic.field_validator("objfunc")
    def validate_objfunc(cls, objfunc):
        valid = ["sos", "sod"]
        # TODO chi_sq does not work atm? why?
        if objfunc not in valid:
            raise ValueError(f"objfunc must be one of {valid}")
        return objfunc

    def generate_pybnf_config_dict(self, func: Callable, data: npt.NDArray[np.float_]):
        config_dict = dict()

        # hacked params
        config_dict["models"] = "np"
        config_dict["_optimization"] = ["_data"]

        config_dict["_custom_func"] = func
        config_dict["_custom_data"] = data

        # general params
        general_params = self.model_dump()
        config_dict.update(general_params)

        # parameter params
        config_dict = self.param_config.update_param_dict(config_dict)

        # algorithm params
        config_dict = self.algorithm_config.update_param_dict(config_dict)

        # clean up unnecessary parameters from
        del config_dict["param_config"]
        del config_dict["algorithm_config"]
        del config_dict["n_params"]
        return config_dict


def run_simple_optimization(func, inputs, outputs, general_config: GeneralConfig):
    """
    Run simple optimization using pyBNF differential evoluation algorithm.
    """
    data = CustomData.from_x_and_y(inputs, outputs)

    # Create parameter dict
    ###########################################################################

    param_dict = general_config.generate_pybnf_config_dict(func, data)
    pybnf_config = CustomConfiguration(param_dict)

    match param_dict["fit_type"]:
        case "de":
            alg = pybnf.algorithms.DifferentialEvolution(pybnf_config)
        case "ade":
            alg = pybnf.algorithms.AsynchronousDifferentialEvolution(pybnf_config)
        case "ss":
            alg = pybnf.algorithms.ScatterSearch(pybnf_config)
        case "pso":
            alg = pybnf.algorithms.ParticleSwarm(pybnf_config)
        case "mh":
            assert param_dict["burn_in"] <= param_dict["max_iterations"]
            assert param_dict["sample_every"] <= param_dict["max_iterations"]
            alg = pybnf.algorithms.BasicBayesMCMCAlgorithm(pybnf_config)
        case "pt":
            assert param_dict["burn_in"] <= param_dict["max_iterations"]
            assert param_dict["sample_every"] <= param_dict["max_iterations"]
            alg = pybnf.algorithms.BasicBayesMCMCAlgorithm(pybnf_config)
        case "sa":
            alg = pybnf.algorithms.BasicBayesMCMCAlgorithm(pybnf_config, sa=True)
        case "am":
            assert param_dict["burn_in"] <= param_dict["max_iterations"]
            assert param_dict["sample_every"] <= param_dict["max_iterations"]
            assert param_dict["adaptive"] <= param_dict["max_iterations"]
            alg = pybnf.algorithms.Adaptive_MCMC(pybnf_config)
        case _:
            raise RuntimeError(f'Unknown fit type: {param_dict["fit_type"]}')

    #######################################################################################

    # TODO set output dir to temporary dir and clean up afterwards
    #
    # TODO investigate how to handle unbound parameters
    # TODO consider whether we only want uniform_var parameters

    # # IMPORTANT: it is necessary to create pybnf_output/Simulations dir!
    # # IMPORTANT: pybnf_output/Results seems also important!
    os.makedirs(
        os.path.join(pybnf_config.config["output_dir"], "Simulations"), exist_ok=True
    )
    os.makedirs(
        os.path.join(pybnf_config.config["output_dir"], "Results"), exist_ok=True
    )

    # TODO check if cluster is actually useful. If not, mock it
    # TODO check arguments for cluster
    cluster = pybnf.cluster.Cluster(pybnf_config, "test", False, "info")
    alg.run(cluster.client, resume=None, debug=False)

    # load results
    # TODO catch any errors during optimization, wrap in scipy.optimize.OptimizeResult
    output = parse_outputs(pybnf_config.config)

    # delete dir
    shutil.rmtree(pybnf_config.config["output_dir"])

    return output


def parse_outputs(config_dir):
    output = dict()
    results = pd.read_table(
        os.path.join(config_dir["output_dir"], "Results", "sorted_params_final.txt")
    )

    output["success"] = True
    # solution to optimization problem
    output["x"] = results.iloc[0, 3:].to_numpy()
    # value of objective function
    output["fun"] = results.iloc[0, 2]

    if config_dir["fit_type"] in [
        "mh",
        "pt",
    ]:
        for interval in config_dir["credible_intervals"]:
            output[f"credible{interval}"] = pd.read_table(
                os.path.join(
                    config_dir["output_dir"], "Results", f"credible{interval}_final.txt"
                )
            )

    return scipy.optimize.OptimizeResult(**output)
