#!/usr/bin/env python3

from setuptools import setup


setup(
    name='ratbag',
    version='0.0.1',
    author='Peter Hutterer',
    author_email='peter.hutterer@redhat.com',
    description='Gaming mouse configuration daemon',
    url='https://github.com/whot/ratbag-python/',
    license='MIT',
    packages=(
        'ratbag',
        'ratbag.cli',
        'ratbag.devices',
        'ratbag.drivers',
    ),
    entry_points={
        'console_scripts': (
            'ratbagd=ratbag.cli.ratbagd:main',
        ),
    },
)
