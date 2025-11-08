from setuptools import setup, find_packages

setup(
    name="leaf-cloud",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "python-hcl2>=3.0.0",
        "pyyaml>=6.0.0",
        "typing-extensions>=4.0.0",
        "numpy>=1.21.0",
        "matplotlib>=3.3.0",
        "pandas>=1.3.0",
        "flask>=2.0.0",
        "flask-cors>=4.0.0",
        "tabulate>=0.8.9"
    ],
    python_requires=">=3.8",
)
