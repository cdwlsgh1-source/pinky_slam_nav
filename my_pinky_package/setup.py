from setuptools import find_packages, setup

package_name = 'my_pinky_package'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='pinky',
    maintainer_email='pinky@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'pinky_patrol_node_pinky1 = my_pinky_package.pinky_patrol_node_pinky1:main',
            'pinky_patrol_node_pinky2 = my_pinky_package.pinky_patrol_node_pinky2:main',
            'pinky_patrol_node_pinky3 = my_pinky_package.pinky_patrol_node_pinky3:main',
        ],
    },
)
