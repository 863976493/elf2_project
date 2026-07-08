from glob import glob
import os

from setuptools import find_packages, setup

package_name = "M3Pro_demo"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test"]) + ["transforms3d"],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob(os.path.join("launch", "*.py"))),
        (os.path.join("share", package_name, "M3Pro_demo"), glob(os.path.join("M3Pro_demo", "*colorHSV.text"))),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="user",
    maintainer_email="user@todo.todo",
    description="Official Yahboom M3Pro color sorting demo isolated for ELF2 testing.",
    license="TODO",
    entry_points={
        "console_scripts": [
            "grasp_desktop = M3Pro_demo.grasp_desktop:main",
            "color_recognize = M3Pro_demo.color_recognize:main",
            "deliver_block = M3Pro_demo.deliver_block:main",
            "place_block = M3Pro_demo.place_block:main",
        ],
    },
)
