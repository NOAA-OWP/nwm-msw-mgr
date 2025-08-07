from setuptools import setup, find_packages

setup(
    name="mswm",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        'geopandas~=1.1.1',
        'pandas~=2.3.1',
        'netCDF4~=1.7.2',
        'pydantic~=2.11.7',
        'PyYAML~=6.0.2',
        'setuptools~=5.6.0'
    ],
)
