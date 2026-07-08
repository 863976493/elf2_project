import os
from glob import glob
from setuptools import find_packages, setup

package_name = "strawberry_mission_bt"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob(os.path.join("launch", "*.py"))),
        (os.path.join("share", package_name, "config"), glob(os.path.join("config", "*.yaml"))),
        (os.path.join("share", package_name, "behavior_trees"), glob(os.path.join("behavior_trees", "*.xml"))),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="user",
    maintainer_email="user@todo.todo",
    description="Mission-level strawberry inspection orchestration.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "inspection_mission_node = strawberry_mission_bt.inspection_mission_node:main",
        ],
    },
)
