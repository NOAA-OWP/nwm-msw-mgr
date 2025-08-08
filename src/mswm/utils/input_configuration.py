"""
This module contains Pydantic classes to validate input.config files for the MSWM

@author: Jeff Wade
"""

from pydantic import BaseModel, root_validator, validator
from pathlib import Path
from typing import Optional, Literal


class StrictBaseModel(BaseModel):
    """
    Custom pydantic BaseModel that checks for absent or empty strings for required variables
    """
    @root_validator(pre=True)
    # Raise errors when required variables are absent or empty strings
    def check_empty_fields(cls, values):
        for field, value in values.items():
            # Check if required variable is none
            if value is None:
                # Retrieve field data
                field_data = cls.__fields__.get(field)
                # Reject variables with none values
                if field_data and not field_data.allow_none:
                    raise ValueError(f"Field '{field}' cannot be empty")

            # Check if string variable is empty
            if isinstance(value, str) and not value.strip():
                # Retrieve field data
                field_data = cls.__fields__.get(field)
                # Reject empty strings only for required fields
                if field_data and not field_data.allow_none:
                    raise ValueError(f"Field '{field}' cannot be an empty string")

            # Check if path variable is empty
            if isinstance(value, Path):
                # Reject empty strs only for required fields
                if str(value).strip() == "":
                    raise ValueError(f"Field '{field}' cannot be an empty str")

        return values


class GeneralConfig(StrictBaseModel):
    """
    Input.config general section requirement for static variables
    """
    basin: str
    run_type: Literal["default", "calibration", "regionalization"]
    models: Optional[str] = None
    main_dir: str
    output_swe: Optional[bool] = None
    output_sm: Optional[bool] = None
    sm_profile_depth: Optional[float] = None
    sm_frac_depth: Optional[float] = None

    # Check optional fields that depend on run_type
    @root_validator
    def check_required_fields(cls, values):
        run_type = values.get("run_type")

        # Models required unless run_type is regionalization
        if run_type != "regionalization" and not values.get("models"):
            raise ValueError("`models` must be specified for a default and calibration runs.")

        return values


class RegionConfig(StrictBaseModel):
    """
    Input.config regionalization section requirement
    """
    form_assign_file: Optional[str]


class CalibConfig(StrictBaseModel):
    """
    Input.config calibration section requirement
    """
    optimization_algorithm: Literal["dds", "pso", "gwo"]
    swarm_size: Optional[int] = None
    c1: Optional[int] = None
    c2: Optional[int] = None
    w: Optional[float] = None
    objective_function: Literal["kge", "nse", "nnse", "nselog", "corr", "csi", "pod",
                                "rmse", "mae", "rsr", "far", "pkbias", "pkte", "evbias",
                                "pbias", "lseg_fdc", "hseg_fdc"]
    start_iteration: int
    number_iteration: int
    restart: int
    calib_start_period: str
    calib_end_period: str
    calib_eval_start_period: str
    calib_eval_end_period: str
    valid_start_period: str
    valid_end_period: str
    valid_eval_start_period: str
    valid_eval_end_period: str
    full_eval_start_period: str
    full_eval_end_period: str
    save_output_iter: int
    save_plot_iter: int
    save_plot_iter_freq: int
    streamflow_threshold: float
    station_name: Optional[str] = None
    ngen_cerf: bool
    calibration_run_id: Optional[int] = None
    auth_token: Optional[str] = None
    user_email: Optional[str] = None
    calib_parameter_file: Optional[str] = None

    # Normalize case of optimization_algorithm
    @validator("optimization_algorithm", pre=True)
    def case_alg(cls, value):
        if isinstance(value, str):
            return value.lower()
        return value

    # Check optional fields that depend on optimization_algoritm
    @root_validator
    def check_required_fields(cls, values):
        opt_alg = values.get("optimization_algorithm")

        # swarm_size required unless opt_alg is DDS
        if opt_alg != "dds" and not values.get("swarm_size"):
            raise ValueError("`swarm_size` must be specified for a PSO or GWO calibration run.")

        # c1 required if opt_alg is PSO
        if opt_alg == "pso" and not values.get("c1"):
            raise ValueError("`c1` must be specified for a PSO calibration run.")

        # c2 required if opt_alg is PSO
        if opt_alg == "pso" and not values.get("c2"):
            raise ValueError("`c2` must be specified for a PSO calibration run.")

        # w required if opt_alg is PSO
        if opt_alg == "pso" and not values.get("w"):
            raise ValueError("`w` must be specified for a PSO calibration run.")

        # restart must be 0 or 1
        if values.get("restart") not in (0, 1):
            raise ValueError("`restart` must be 0 or 1.")

        # save_output_iter must be 0 or 1
        if values.get("save_output_iter") not in (0, 1):
            raise ValueError("`save_output_iter` must be 0 or 1.")

        # save_plot_iter must be 0 or 1
        if values.get("save_plot_iter") not in (0, 1):
            raise ValueError("`save_plot_iter` must be 0 or 1.")

        return values


class DataFileConfig(StrictBaseModel):
    """
    Input.config DataFile section requirement
    """
    obs_dir: Optional[str] = None
    nwmretro_file: Optional[str] = None
    hydrofab_file: str
    topoflow_bmi_dir: Optional[str] = None
    noah_owp_modular_bmi_dir: Optional[str] = None
    snow_17_bmi_dir: Optional[str] = None
    ueb_bmi_dir: Optional[str] = None
    pet_bmi_dir: Optional[str] = None
    smp_bmi_dir: Optional[str] = None
    sft_bmi_dir: Optional[str] = None
    cfe_s_bmi_dir: Optional[str] = None
    cfe_x_bmi_dir: Optional[str] = None
    topmodel_bmi_dir: Optional[str] = None
    sac_sma_bmi_dir: Optional[str] = None
    lasam_bmi_dir: Optional[str] = None
    t_route_bmi_dir: Optional[str] = None
    noah_parameter_dir: Optional[str] = None
    ueb_parameter_dir: Optional[str] = None
    lasam_parameter_dir: Optional[str] = None
    sac_parameter_dir: Optional[str] = None
    snow_17_parameter_dir: Optional[str] = None
    attributes_file: str
    ngen_exe_file: str
    sloth_lib: Optional[str] = None
    cfe_lib: Optional[str] = None
    lasam_lib: Optional[str] = None
    noah_owp_modular_lib: Optional[str] = None
    pet_lib: Optional[str] = None
    sac_sma_lib: Optional[str] = None
    sft_lib: Optional[str] = None
    smp_lib: Optional[str] = None
    snow_17_lib: Optional[str] = None
    topmodel_lib: Optional[str] = None
    ueb_lib: Optional[str] = None


class ParallelConfig(StrictBaseModel):
    """
    Input.config Parallel section requirement
    """
    parallel_ngen_exe: str
    partition_generator_exe: str
    nprocs: int


class InputConfig(StrictBaseModel):
    """
    Class to organize input.config section requirements
    """
    General: GeneralConfig
    Regionalization: Optional[RegionConfig] = None
    Calibration: Optional[CalibConfig] = None
    DataFile: DataFileConfig
    Parallel: Optional[ParallelConfig] = None

    # Check optional sections are present
    # Root_validator skips if another value fails
    @root_validator(skip_on_failure=True)
    def check_required_sections(cls, values):
        run_type = values["General"].run_type

        # Regionalization section required for regionalization run
        if run_type == "regionalization" and values.get("Regionalization") is None:
            raise ValueError("Regionalization section is required for regionalization runs.")

        # Calibration section required for calibration run
        if run_type == "calibration" and values.get("Calibration") is None:
            raise ValueError("Calibration section is required for calibration runs.")

        return values
