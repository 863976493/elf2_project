import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'strawberry_tracker'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'),
            glob(os.path.join('launch', '*.py*'))),
        (os.path.join('share', package_name, 'config'),
            glob(os.path.join('config', '*.yaml'))),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='user',
    maintainer_email='user@todo.todo',
    description='Strawberry visual servo tracking for Yahboom M3 Pro',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'strawberry_tracker_node = strawberry_tracker.strawberry_tracker_node:main',
        ],
    },
)
