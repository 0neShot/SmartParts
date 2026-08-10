import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="inventree-smart-parts",
    version="1.1.1",
    author="0neShot",
    description="Intelligent inventory assistant for InvenTree that automates part creation from MPN lookup.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/0neShot/SmartParts",
    packages=setuptools.find_packages(
        include=["inventree_smart_parts", "inventree_smart_parts.*"]
    ),
    include_package_data=True,
    install_requires=[
        "requests>=2.25.0",
        "openpyxl>=3.0.0",
    ],
    entry_points={
        "inventree_plugins": [
            "SmartPartsPlugin = inventree_smart_parts.core:SmartPartsPlugin"
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.9",
)
