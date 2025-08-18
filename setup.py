from setuptools import find_packages, setup

setup(
    name="mswm",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "geopandas~=1.1.1",
        "pandas~=2.3.1",
        "netCDF4==1.6.3",
        "pydantic==2.11.4",
        "PyYAML~=6.0.2"
    ],
)
