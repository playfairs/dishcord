from setuptools import find_packages, setup

setup(
    name="dishcord",
    version="0.1.0",
    description="Interactive shell sessions for discord.py bots",
    python_requires=">=3.9",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    include_package_data=True,
    install_requires=["discord.py>=2.6.0"],
)
