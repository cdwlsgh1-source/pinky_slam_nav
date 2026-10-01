import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'zone_traffic_control'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.xml')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='your_name',
    maintainer_email='you@example.com',
    description='Nav2 다중 로봇 구역(zone) 상호 배제 트래픽 제어 패키지',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'zone_manager_node = zone_traffic_control.zone_manager_node:main',
            'zone_manager_node_v2 = zone_traffic_control.zone_manager_node_v2:main',
        ],
    },
)
