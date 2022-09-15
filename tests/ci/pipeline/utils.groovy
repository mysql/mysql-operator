// Copyright (c) 2022, Oracle and/or its affiliates.
//
// Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
//

def getEnterpriseImageInfo(String operatorEnterpriseImage) {
	if (operatorEnterpriseImage) {
		return operatorEnterpriseImage
	}
	return "not specified, it will be built internally"
}

def isCIExperimentalBuild() {
	final CI_EXPERIMENTAL_BRANCH_PREFIX = 'ci/experimental/'
	return params.OPERATOR_GIT_REVISION.contains(CI_EXPERIMENTAL_BRANCH_PREFIX)
}

def initEnv() {
	env.WORKERS_FOLDER = 'Shell/KubernetesOperator/' + "${isCIExperimentalBuild() ? 'sandbox' : 'workers'}"
	env.BUILD_TRIGGERED_BY = "${params.OPERATOR_INTERNAL_BUILD ? 'internal' : 'concourse'}"
	env.LOG_SUBDIR = "build-${BUILD_NUMBER}"
	env.LOG_DIR = "${WORKSPACE}/${LOG_SUBDIR}"
	env.ARTIFACT_FILENAME = "result-${JOB_BASE_NAME}-${BUILD_NUMBER}.tar.bz2"
	env.ARTIFACT_PATH = "${WORKSPACE}/${ARTIFACT_FILENAME}"

	env.SLACK_CHANNEL = "${isCIExperimentalBuild() ? '#mysql-operator-ci' : '#mysql-operator-dev'}"
	env.BUILD_NOTIFICATION_HEADER = "${currentBuild.fullDisplayName} (<${env.BUILD_URL}|Open>)"

	env.GIT_AUTHOR_DATE = sh (script: "git log -1 --pretty='%an <%ae>, %ad' ${GIT_COMMIT}", returnStdout: true).trim()
	env.GIT_BRANCH_NAME = sh (script: "git name-rev --name-only ${GIT_COMMIT}", returnStdout: true).trim()
	env.GIT_COMMIT_SUBJECT = sh (script: "git log -1 --pretty=%s ${GIT_COMMIT}", returnStdout: true).trim()
	env.GIT_COMMIT_SHORT = sh (script: "git rev-parse --short HEAD", returnStdout: true).trim()
	env.ENTERPRISE_IMAGE_INFO = getEnterpriseImageInfo("${params.OPERATOR_ENTERPRISE_IMAGE}")
}

def getInitMessage() {
	return """${env.BUILD_NOTIFICATION_HEADER}
${currentBuild.getBuildCauses().shortDescription} (${env.BUILD_TRIGGERED_BY})
Branch/Revision: ${env.GIT_BRANCH_NAME} ${params.OPERATOR_GIT_REVISION}
Image: ${params.OPERATOR_IMAGE}
Enterprise Image: ${env.ENTERPRISE_IMAGE_INFO}
The latest commit:
${env.GIT_AUTHOR_DATE}
${env.GIT_COMMIT} [hash: ${env.GIT_COMMIT_SHORT}]
${env.GIT_COMMIT_SUBJECT}"""
}

def addTestResults(String k8s_env) {
	sh "ls ${env.LOG_DIR}"
	def testResults = findFiles glob: "**/${env.LOG_SUBDIR}/result-$k8s_env-*.tar.bz2"
	if (testResults.length == 0) {
		return false
	}

	def resultPattern = "${env.LOG_DIR}/result-$k8s_env-*.tar.bz2"
	sh "cat $resultPattern | tar jxvf - -i -C ${env.LOG_DIR} && rm $resultPattern"

	// uncomment during Jenkins refactorings when some jobs are intentionally skipped
	// sh "touch ${LOG_DIR}/xml/*.xml"

	def summary = junit allowEmptyResults: true, testResults: "${env.LOG_SUBDIR}/xml/$k8s_env-*.xml"
	echo "${summary.totalCount} tests, ${summary.passCount} passed, ${summary.failCount} failed, ${summary.skipCount} skipped"
	return summary.totalCount > 0
}

def anyResultsAvailable() {
	return env.MINIKUBE_RESULTS_AVAILABLE || env.K3D_RESULTS_AVAILABLE;
}

def getIssuesReport(String k8s_env) {
	def issuesReportPath = "${env.LOG_DIR}/${k8s_env}-issues.log"
	def issuesReportExists = fileExists issuesReportPath
	if (!issuesReportExists) {
		return ""
	}

	def issuesReport = readFile(file: issuesReportPath)
	echo issuesReport
	return issuesReport
}

def getTestSuiteReport() {
	sh "ls ${env.LOG_DIR}"
	def testSuiteReportFiles = findFiles glob: "**/${env.LOG_SUBDIR}/test_suite_report_*.tar.bz2"
	if (testSuiteReportFiles.length == 0) {
		return ""
	}

	def reportPattern = "${env.LOG_DIR}/test_suite_report_*.tar.bz2"
	sh "cat $reportPattern | tar jxvf - -i -C ${env.LOG_DIR} && rm $reportPattern"

	def testSuiteReport = getIssuesReport('k3d') + getIssuesReport('minikube')

	def reportPath = "${env.LOG_DIR}/test_suite_report.txt"
	def reportExists = fileExists reportPath
	if (!reportExists) {
		return testSuiteReport
	}

	def briefReportPath = "${env.LOG_DIR}/test_suite_brief_report.txt"
	def ReportedIssuesMaxCount = 10
	sh "cat $reportPath | sed -ne '1,$ReportedIssuesMaxCount p' -e '${ReportedIssuesMaxCount+1} iand more...' > $briefReportPath"

	testSuiteReport += readFile(file: briefReportPath)
	echo testSuiteReport
	return testSuiteReport
}

def getTestsStatusHeader() {
	sh 'ls -lRF ${LOG_DIR}'
	testStatusHeader = "Test suite:"
	if (anyResultsAvailable()) {
		if (!env.MINIKUBE_RESULTS_AVAILABLE) {
			testStatusHeader += "\nNo test results for minikube!"
		} else if (env.SOME_MINIKUBE_RESULTS_UNAVAILABLE) {
			testStatusHeader += "\nSome test results for minikube unavailable!"
		}

		if (!env.K3D_RESULTS_AVAILABLE) {
			testStatusHeader += "\nNo test results for k3d!"
		} else if (env.SOME_K3D_RESULTS_UNAVAILABLE) {
			testStatusHeader += "\nSome test results for k3d unavailable!"
		}
	} else {
		testStatusHeader = "\nNo test results available!"
	}
	return testStatusHeader
}

def getBuildDuration() {
	return "${currentBuild.durationString.minus(' and counting')}"
}

def getChangeLog() {
	def changeSets = currentBuild.changeSets
	if (!changeSets.size()) {
		return "No changes\n"
	}

	def changeLog = "Changes:\n"
	for (int i = 0; i < changeSets.size(); ++i) {
		def entries = changeSets[i].items
		for (int j = 0; j < entries.length; ++j) {
			def entry = entries[j]
			changeLog += "${entry.msg} [${entry.author}, ${new Date(entry.timestamp)}]\n"
		}
	}
	return changeLog
}

@NonCPS
def modifyBuildStatus(String status) {
	if (!env.BUILD_STATUS) {
		env.BUILD_STATUS = status
	} else {
		env.BUILD_STATUS += ", " + status
	}
}

def getSummaryResult() {
	if (!env.INIT_STAGE_SUCCEEDED) {
		return "Init (local registry) stage failed!"
	}

	if (params.OPERATOR_INTERNAL_BUILD && !env.BUILD_STAGE_SUCCEEDED) {
		return "Build dev-images stage failed!"
	}

	if (env.TESTS_STATUS_HEADER && env.TEST_SUITE_REPORT) {
		return "${env.TESTS_STATUS_HEADER}\n${env.TEST_SUITE_REPORT}"
	}

	return "Test stage failed!"
}

def getSummaryMessage(Boolean includeChangeLog) {
	changeLog = includeChangeLog ? "${env.CHANGE_LOG}\n" : ""
	return """${env.BUILD_NOTIFICATION_HEADER}
Status: ${env.BUILD_STATUS}
Duration: ${env.BUILD_DURATION}
$changeLog${getSummaryResult()}"""
}

return this
