from setuptools import find_packages, setup
import os
from glob import glob
package_name = 'multi_brains'

# 创建保留目录结构的data_files列表
def data_files_with_structure(source_dir, target_dir):
    files = []
    for root, dirs, filenames in os.walk(source_dir):
        dir_path = os.path.relpath(root, source_dir)
        if dir_path == '.':
            target_path = target_dir
        else:
            target_path = os.path.join(target_dir, dir_path)
        file_list = [os.path.join(root, f) for f in filenames]
        files.append((target_path, file_list))
    return files


setup(
    name=package_name,
    version='1.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
        (os.path.join('share', package_name, 'language'), glob('language/*.yaml')),
    ]+ data_files_with_structure('system_vioce', os.path.join('share', package_name, 'system_vioce')),
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='jetson',
    maintainer_email='jetson-nx@example.com',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={'test': ['pytest'],},
    entry_points={
        'console_scripts': [
            'model_service=multi_brains.model_service:main',
            'action_service=multi_brains.action_service:main',
        ],
    },
)
