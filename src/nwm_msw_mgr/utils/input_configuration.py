"""
This module contains Pydantic classes to validate input.config files for the NWM-MSW-MGR

@author: Jeff Wade
"""

from pydantic import BaseModel, model_validator, field_validator
from pathlib import Path
from typing import Optional, Literal


class StrictBaseModel(BaseModel):
    """
    Custom pydantic BaseModel that checks for absent or empty strings for required variables
    """
    @model_validator(mode="before")
    # Raise errors when required variables are absent or empty strings
    def check_empty_fields(cls, values):
        for field, value in values.items():
            # Check if string variable is empty
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"Field '{field}' cannot be an empty string")
            # Check if path variable is empty
            if isinstance(value, Path) and not str(value).strip():
                raise ValueError(f"Field '{field}' cannot be an empty Path")

        return values


class GeneralConfig(StrictBaseModel):
    """
    Input.config general section requirement
    """
    basin: str
    run_type: Literal["default", "calibration", "regionalization"]
    models: Optional[str] = None
    formulation: Optional[str] = None
    main_dir: str
    start_period: Optional[str] = None
    end_period: Optional[str] = None
    output_swe: Optional[bool] = None
    output_sm: Optional[bool] = None
    sm_profile_depth: Optional[float] = None
    sm_frac_depth: Optional[float] = None

    # Check optional fields that depend on run_type
    @model_validator(mode="after")
    def check_required_fields(self):
        # Models required unless run_type is regionalization
        if self.run_type != "regionalization" and not self.models:
            raise ValueError("`models` must be specified for a default and calibration runs.")

        # Start_period and end_period required unless run_type is calibration
        if self.run_type != "calibration" and (not self.start_period or not self.end_period):
            raise ValueError("`start_period` and `end_period` must be specified for default and regionalization runs.")

        return self


class RegionConfig(StrictBaseModel):
    """
    Input.config regionalization section requirement
    """
    form_assign_file: str


class CalibConfig(StrictBaseModel):
    """
    Input.config calibration section requirement
    """
    optimization_algorithm: Optional[Literal["dds", "pso", "gwo"]] = None
    swarm_size: Optional[int] = None
    c1: Optional[int] = None
    c2: Optional[int] = None
    w: Optional[float] = None
    objective_function: Optional[Literal["kge", "nse", "nnse", "nselog", "corr", "csi", "pod",
                                         "rmse", "mae", "rsr", "far", "pkbias", "pkte", "evbias",
                                         "pbias", "lseg_fdc", "hseg_fdc"]] = None
    start_iteration: Optional[int] = None
    number_iteration: Optional[int] = None
    restart: Optional[int] = None
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
    save_output_iter: Optional[int] = None
    save_plot_iter: Optional[int] = None
    save_plot_iter_freq: Optional[int] = None
    streamflow_threshold: Optional[float] = None
    station_name: Optional[str] = None
    ngen_cerf: bool
    calibration_run_id: Optional[int] = None
    auth_token: Optional[str] = None
    user_email: Optional[str] = None
    calib_parameter_file: Optional[str] = None

    # Normalize case of optimization_algorithm
    @field_validator("optimization_algorithm", mode="before")
    def case_alg(cls, value):
        if isinstance(value, str):
            return value.lower()
        return value

    # Check optional fields that depend on optimization_algoritm
    @model_validator(mode="after")
    def check_required_fields(self):

        # swarm_size required unless optimization_algorithm is DDS
        if self.optimization_algorithm is not None and self.optimization_algorithm != "dds" and not self.swarm_size:
            raise ValueError("`swarm_size` must be specified for a PSO or GWO calibration run.")

        # c1 required if optimization_algorithm is PSO
        if self.optimization_algorithm is not None and self.optimization_algorithm == "pso" and not self.c1:
            raise ValueError("`c1` must be specified for a PSO calibration run.")

        # c2 required if optimization_algorithm is PSO
        if self.optimization_algorithm is not None and self.optimization_algorithm == "pso" and not self.c2:
            raise ValueError("`c2` must be specified for a PSO calibration run.")

        # w required if optimization_algorithm is PSO
        if self.optimization_algorithm is not None and self.optimization_algorithm == "pso" and not self.w:
            raise ValueError("`w` must be specified for a PSO calibration run.")

        # restart must be 0 or 1
        if self.restart is not None and self.restart not in (0, 1):
            print(self.restart)
            raise ValueError("`restart` must be 0 or 1.")

        # save_output_iter must be 0 or 1
        if self.save_output_iter is not None and self.save_output_iter not in (0, 1):
            raise ValueError("`save_output_iter` must be 0 or 1.")

        # save_plot_iter must be 0 or 1
        if self.save_plot_iter is not None and self.save_plot_iter not in (0, 1):
            raise ValueError("`save_plot_iter` must be 0 or 1.")

        return self


class DataFileConfig(StrictBaseModel):
    """
    Input.config DataFile section requirement
    """
    forcing_dir: str
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
    lstm_bmi_dir: Optional[str] = None
    t_route_bmi_dir: Optional[str] = None
    noah_parameter_dir: Optional[str] = None
    ueb_parameter_dir: Optional[str] = None
    lasam_parameter_dir: Optional[str] = None
    lstm_parameter_dir: Optional[str] = None
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
    @model_validator(mode="after")
    def check_required_sections(self):
        run_type = self.General.run_type

        # Regionalization section required for regionalization run
        if run_type == "regionalization" and self.Regionalization is None:
            raise ValueError("Regionalization section is required for regionalization runs.")

        # Calibration section required for calibration run
        if run_type == "calibration" and self.Calibration is None:
            raise ValueError("Calibration section is required for calibration runs.")

        return self
