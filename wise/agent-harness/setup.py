from setuptools import find_namespace_packages, setup

setup(
    name="cli-anything-wise",
    version="1.0.0",
    description="Wise (TransferWise) CLI - balances, rates, quotes, recipients and transfers via the Wise Platform API",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="CLI-Anything Community",
    url="https://github.com/HKUDS/CLI-Anything",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    entry_points={
        "console_scripts": [
            "cli-anything-wise=cli_anything.wise.wise_cli:main",
        ],
    },
    package_data={"cli_anything.wise": ["skills/*.md", "tests/*.md"]},
    install_requires=[
        "click>=8.0",
        "prompt_toolkit>=3.0",
        "requests>=2.25",
    ],
    extras_require={
        "sca": ["cryptography>=41"],
        "dev": ["pytest>=7.0", "cryptography>=41"],
    },
    python_requires=">=3.10",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Programming Language :: Python :: 3",
        "Topic :: Office/Business :: Financial",
    ],
    keywords="wise transferwise payments fx cli cli-anything",
)
