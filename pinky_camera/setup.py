import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'pinky_camera'

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
    description='Pinky 로봇 CSI 카메라(picamera2) 영상을 ROS2 CompressedImage 토픽으로 발행하는 패키지',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'camera_node = pinky_camera.camera_node:main',
            'lane_detector_node = pinky_camera.lane_detector_node:main',
            'lane_follower_node = pinky_camera.lane_follower_node:main',
        ],
    },
)