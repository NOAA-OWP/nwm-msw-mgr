"""
This module creates a SetupManager to manage the modification of configuration files for individual ngen runs

@author: Jeffrey Wade
"""

import argparse
from nwm_msw_mgr.build_inputs import RealizationBuilder


def build_default(input_path: str):
    """
    Call RealizationBuilder class to generate realization and config files with default parameters
    """
    rb = RealizationBuilder(input_path)
    rb.build_default_realization()


def build_calib(input_path: str):
    """
    Call RealizationBuilder class to generate initial calibration realization and config files
    """
    rb = RealizationBuilder(input_path)
    rb.build_calib_realization()


def build_fcst(input_path: str, forcing_path: str, output_folder: str):
    """
    Call RealizationBuilder class to generate forecast realization and config files
    """
    rb = RealizationBuilder(input_path, forcing_path, output_folder)
    rb.build_fcst_realization()


def build_region(input_path: str, assign_path: str):
    """
    Call RealizationBuilder class to generate realization and config files for regionalization
    """
    rb = RealizationBuilder(input_path, assign_path)
    rb.build_region_realization()


def main():
    # Create command line parser
    parser = argparse.ArgumentParser(prog="nwm_msw_mgr",
                                     description="Model Setup Workflow Manager command-line")
    subparser = parser.add_subparsers(dest="command", required=True, help="Available commands")

    # subcommand: build_default
    build_default_sub = subparser.add_parser("build_default", help="Create default realization")
    build_default_sub.add_argument("input_path", help="Input configuration file")

    # subcommand: build_calib
    build_calib_sub = subparser.add_parser("build_calib", help="Create calibration realization")
    build_calib_sub.add_argument("input_path", help="Input configuration file")

    # subcommand: build_region
    build_region_sub = subparser.add_parser("build_region", help="Create regionalization realization")
    build_region_sub.add_argument("input_path", help="Input configuration file")
    build_region_sub.add_argument("assign_path", help="Formulation assignment file")

    # subcommand: build_fcst
    build_fcst_sub = subparser.add_parser("build_fcst", help="Create forecast realization")
    build_fcst_sub.add_argument("input_path", help="Path to the config yaml file for a validation run")
    build_fcst_sub.add_argument("forcing_path", help="Path to the NetCDF forcing file OR "
                                                     "a folder containing .csv forcing files for all catchments")
    build_fcst_sub.add_argument("output_folder", help="Path to the folder to be created for storing inputs/outputs from running ngen")

    args = parser.parse_args()

    # Parser logic
    if args.command == "build_default":
        build_default(args.input_path)
    elif args.command == "build_calib":
        build_calib(args.input_path)
    elif args.command == "build_region":
        build_region(args.input_path, args.assign_path)
    elif args.command == "build_fcst":
        build_fcst(args.input_path, args.forcing_path, args.output_folder)
    else:
        raise ValueError(f"Unexpected nwm-msw-mgr command: {args.command}")


if __name__ == "__main__":
    main()
