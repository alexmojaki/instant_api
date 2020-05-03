#!/usr/bin/env bash
set -eux

if [ -z "${1+x}" ] then
    set +x
    echo Provide a version argument
    echo "${0} <major>.<minor>.<patch>"
    exit 1
else
    if [[ ${1} =~ ^v?([0-9]+)(\.[0-9]+)?(\.[0-9]+)?$ ]] then
        :
    else
        echo "Not a valid release tag."
        exit 1
    fi
fi

export TAG="v${1}"
git tag "${TAG}"
git push origin master "${TAG}"
rm -rf ./build ./dist
python3 -m pep517.build -b .
twine upload ./dist/*.whl
