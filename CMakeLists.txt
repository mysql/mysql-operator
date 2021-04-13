# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

SET(DEPLOY_SOURCES
    ${CMAKE_CURRENT_SOURCE_DIR}/deploy/deploy-crd-peerings.yaml
    ${CMAKE_CURRENT_SOURCE_DIR}/deploy/deploy-crds.yaml
    ${CMAKE_CURRENT_SOURCE_DIR}/deploy/deploy-sa.yaml
    ${CMAKE_CURRENT_SOURCE_DIR}/deploy/deploy-rbac.yaml
    ${CMAKE_CURRENT_SOURCE_DIR}/deploy/deploy-peering.yaml
    ${CMAKE_CURRENT_SOURCE_DIR}/deploy/deploy-operator.yaml
    )

FUNCTION(make_deploy_all)
  SET(everything "")
  FOREACH(file ${DEPLOY_SOURCES})
    FILE(READ "${file}" data)
    IF(everything)
      SET(everything "${everything}\n---\n${data}")
    ELSE()
      SET(everything "${data}")
    ENDIF()
  ENDFOREACH()
  FILE(WRITE ${CMAKE_CURRENT_SOURCE_DIR}/deploy/deploy-all.yaml "${everything}")
ENDFUNCTION()

make_deploy_all()
