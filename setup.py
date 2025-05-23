from setuptools import setup, find_packages

pkg_name = "QDL"


def read_file(fname):
    with open(fname, "r") as f:
        return f.read()


requirements = [
    "pathvalidate",
    "requests",
    "mutagen",
    "tqdm",
    "pick==1.6.0",
    "beautifulsoup4",
    "colorama",
    "keyboard",
]

setup(
    name=pkg_name,
    version="0.15.0",
    author="Dragonherb",
    author_email="dragonherbdj@gmail.com",
    description="The complete Lossless and Hi-Res music downloader for Qobuz",
    long_description=read_file("README.md"),
    long_description_content_type="text/markdown",
    url="https://github.com/dragonherb/qdl",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "QDL = qobuz_downloader:main",
            "qdl = qobuz_downloader:main",
        ],
    },
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License (GPL)",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)

# rm -f dist/*
# python3 setup.py sdist bdist_wheel
# twine upload dist/*
