// Copyright (c) 2022,2023, Oracle and/or its affiliates.
//
// Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
//

def isCIExperimentalBuild() {
	final CI_EXPERIMENTAL_BRANCH_PREFIX = 'ci/experimental/'
	if (params.OPERATOR_GIT_REVISION.contains(CI_EXPERIMENTAL_BRANCH_PREFIX) ||
		params.OPERATOR_GIT_BRANCH.contains(CI_EXPERIMENTAL_BRANCH_PREFIX)) {
		return true
	}

	final INSIDE_SANDBOX_JOB_FOLDER = '/sandbox/'
	if (env.JOB_NAME.contains(INSIDE_SANDBOX_JOB_FOLDER)) {
		return true
	}

	return false
}

def isGerritBuild() {
	return env.BUILD_TRIGGERED_BY == 'gerrit'
}

def getTriggeredBy(String triggeredBy) {
	if (triggeredBy) {
		return triggeredBy
	}
	return 'concourse'
}

def hasValue(def variable) {
	return variable != null && variable != 'undefined' && variable != ''
}

def getGitBranchName() {
	if (isGerritBuild() && params.OPERATOR_GERRIT_TOPIC) {
		return params.OPERATOR_GERRIT_TOPIC
	}

	if (params.OPERATOR_GIT_BRANCH) {
		return params.OPERATOR_GIT_BRANCH
	}

	def resolvedGitBranchName = sh (script: "git name-rev --name-only ${GIT_COMMIT}", returnStdout: true).trim()
	if (hasValue(resolvedGitBranchName)) {
		return resolvedGitBranchName
	}

	resolvedGitBranchName = sh (script: 'git rev-parse --abbrev-ref HEAD', returnStdout: true).trim()
	if (hasValue(resolvedGitBranchName) && (resolvedGitBranchName != 'HEAD')) {
		return resolvedGitBranchName
	}

	return env.GIT_BRANCH
}

def getExecutionEnvironment(String defaultExecEnv) {
	if (hasValue(params.OPERATOR_EXECUTION_ENVIRONMENT)) {
		return params.OPERATOR_EXECUTION_ENVIRONMENT
	}
	return defaultExecEnv
}

def prepareCommunityImage(String operatorImage) {
	return operatorImage.replace("mysql-operator", "community-operator")
}

def getImageInfo(String operatorImage) {
	if (operatorImage) {
		return operatorImage
	}
	return "not specified, it will be built locally"
}

def yesOrNo(boolean flag) {
	return flag ? "yes" : "no"
}

def countFilesInSubdir(String subdir) {
	def files = findFiles glob: "**/${subdir}/*"
	echo "${subdir} contains ${files.length} file(s)"
}

def listFilesInSubdir(String subdir) {
	def files = findFiles glob: "**/${subdir}/*"
	def dirContents = "${subdir} contains ${files.length} file(s):\n"
	files.each { file ->
		dirContents += "${file.name} ${file.length} byte(s)\n"
	}
	echo dirContents
}

def prepareCredentials(String credentialsDir) {
	if (!fileExists(credentialsDir)) {
		error "The directory with credentials ${credentialsDir} does not exist!"
	}

	return sh(script: "tar cpj --owner=\$USER --group=\$(id -gn) -C ${credentialsDir} . | base64", returnStdout: true)
}

def initEnv() {
	env.INIT_STAGE_SUCCEEDED = false
	env.BUILD_STAGE_SUCCEEDED = false
	env.MINIKUBE_RESULT_STATUS = env.TEST_RESULTS_UNAVAILABLE
	env.K3D_RESULT_STATUS = env.TEST_RESULTS_UNAVAILABLE
	env.KIND_RESULT_STATUS = env.TEST_RESULTS_UNAVAILABLE
	env.TEST_SUITE_REPORT = ""
	env.TESTS_SUITE_ISSUES = ""
	env.BUILD_DURATION = ""
	env.CHANGE_LOG = ""
	env.BUILD_STATUS = ""

	env.WORKERS_FOLDER = 'Shell/KubernetesOperator/' + "${isCIExperimentalBuild() ? 'sandbox' : 'workers'}"
	env.BUILD_TRIGGERED_BY = getTriggeredBy(params.OPERATOR_TRIGGERED_BY)

	env.TESTS_DIR = "${WORKSPACE}/tests"
	env.CI_DIR = "${env.TESTS_DIR}/ci"
	env.TESTS_SUBDIR = "./tests"
	env.CI_SUBDIR = "${env.TESTS_SUBDIR}/ci"
	env.LOG_SUBDIR = "build-${BUILD_NUMBER}"
	env.LOG_DIR = "${WORKSPACE}/${env.LOG_SUBDIR}"
	env.INSTANCES_SUBDIR = "${env.LOG_SUBDIR}/instances"
	env.INSTANCES_DIR = "${WORKSPACE}/${env.INSTANCES_SUBDIR}"
	env.INSTANCE_TESTSUITE_INFIX = "instance-suite"
	env.ARTIFACT_FILENAME = "${JOB_BASE_NAME}-${BUILD_NUMBER}-result.tar.bz2"
	env.ARTIFACT_PATH = "${WORKSPACE}/${ARTIFACT_FILENAME}"

	env.SLACK_CHANNEL = "${isCIExperimentalBuild() ? '#mysql-operator-ci' : '#mysql-operator-dev'}"
	env.BUILD_NOTIFICATION_HEADER = "${currentBuild.fullDisplayName} (<${env.BUILD_URL}|Open>)"
	env.COLOR_INFO = '#808080'

	env.GIT_AUTHOR_DATE = sh (script: "git log -1 --pretty='%an <%ae>, %ad' ${GIT_COMMIT}", returnStdout: true).trim()
	env.GIT_BRANCH_NAME = getGitBranchName()
	env.GIT_COMMIT_SUBJECT = sh (script: "git log -1 --pretty=%s ${GIT_COMMIT}", returnStdout: true).trim()
	env.GIT_COMMIT_SHORT = sh (script: "git rev-parse --short HEAD", returnStdout: true).trim()

	env.OPERATOR_COMMUNITY_IMAGE = prepareCommunityImage("${params.OPERATOR_IMAGE}")

	env.BASE_IMAGE_INFO = getImageInfo("${params.OPERATOR_IMAGE}")
	env.COMMUNITY_IMAGE_INFO = getImageInfo("${env.OPERATOR_COMMUNITY_IMAGE}")
	env.ENTERPRISE_IMAGE_INFO = getImageInfo("${params.OPERATOR_ENTERPRISE_IMAGE}")

	env.EXECUTION_ENVIRONMENT_LOCAL = 'local'
	env.EXECUTION_ENVIRONMENT_OCI = 'oci'
	// for backward compatibility, if params.OPERATOR_EXECUTION_ENVIRONMENT is not set then treat it as 'local'
	env.EXECUTION_ENVIRONMENT = getExecutionEnvironment(env.EXECUTION_ENVIRONMENT_LOCAL)

	env.TEST_RESULTS_UNAVAILABLE = 'UNAVAILABLE'
	env.TEST_RESULTS_SOME_AVAILABLE = 'SOME_AVAILABLE'
	env.TEST_RESULTS_ALL_AVAILABLE = 'ALL_AVAILABLE'

	// can't use enum type, see eventum ticket #79024: jenkins: problem with enum type in groovy code
	// enum TestSuiteReportPart {
	// 	Summary,
	// 	Failures,
	// 	Skipped
	// }
	env.REPORT_PART_SUMMARY = 'SUMMARY'
	env.REPORT_PART_FAILURES = 'FAILURES'
	env.REPORT_PART_SKIPPED = 'SKIPPED'

	env.OTE_CREDENTIALS = prepareCredentials(env.OTE_CREDENTIALS_DIR)
}

def isExecutionEnvironment(String execEnvType) {
	return env.EXECUTION_ENVIRONMENT == execEnvType
}

def isLocalExecutionEnvironment() {
	return isExecutionEnvironment(env.EXECUTION_ENVIRONMENT_LOCAL)
}

def isOciExecutionEnvironment() {
	return isExecutionEnvironment(env.EXECUTION_ENVIRONMENT_OCI)
}

def getIntroHeader() {
	return """${env.BUILD_NOTIFICATION_HEADER}
${currentBuild.getBuildCauses().shortDescription} (flow: ${env.BUILD_TRIGGERED_BY}, execution: ${env.EXECUTION_ENVIRONMENT})
Branch: ${env.GIT_BRANCH_NAME}
Revision: ${params.OPERATOR_GIT_REVISION}"""
}

def getLatestCommit() {
	return """Latest commit
${env.GIT_AUTHOR_DATE}
${env.GIT_COMMIT} [hash: ${env.GIT_COMMIT_SHORT}]
${env.GIT_COMMIT_SUBJECT}"""
}

def getImagesInfo() {
	return """Images
Base: ${env.BASE_IMAGE_INFO}
Community: ${env.COMMUNITY_IMAGE_INFO}
Enterprise: ${env.ENTERPRISE_IMAGE_INFO}
Allow weekly images: ${yesOrNo(params.OPERATOR_ALLOW_WEEKLY_IMAGES)}"""
}

def getGerritCRInfo() {
	return """Code Review (<${params.OPERATOR_GERRIT_CHANGE_URL}|Open>)
Change: ${params.OPERATOR_GERRIT_CHANGE_NUMBER}
Patchset: #${params.OPERATOR_GERRIT_PATCHSET_NUMBER}
Change-ID: ${params.OPERATOR_GERRIT_CHANGE_ID}"""
}

def getIntroColor() {
	return env.COLOR_INFO
}

def getIntroContents() {
	def introColor = getIntroColor()

	def attachments = [
		[
			text: getIntroHeader(),
			color: introColor
		],
		[
			text: getLatestCommit(),
			color: introColor
		],
		[
			text: getImagesInfo(),
			color: introColor
		]
	]

	if (isGerritBuild()) {
		attachments.add([
			text: getGerritCRInfo(),
			color: introColor
		])
	}

	return attachments
}

def canRunOnOci(String k8sEnv) {
	// currently, on OCI, we do support minikube only
	switch(k8sEnv) {
		case 'minikube':
			return true

		case 'k3d':
		case 'kind':
			return false

		default:
			echo "Unknown k8s environment ${k8sEnv}!"
			return false
	}
}

def getWorkerJobPath(String k8sEnv) {
	def workerJobPath = "${env.WORKERS_FOLDER}/${k8sEnv}"
	if (isOciExecutionEnvironment() && canRunOnOci(k8sEnv)) {
		workerJobPath += "-oci"
	}
	return workerJobPath
}

def getK8sEnvBinaryPath(String k8sEnv, String preferredK8sEnvBinaryPath) {
	// we have custom k8s-env binaries like e.g. minikube-v1.31.2, k3d-v5.6.0, or
	// kind-v0.20.0 on our local CI machine only
	if (isLocalExecutionEnvironment() || !canRunOnOci(k8sEnv)) {
		return preferredK8sEnvBinaryPath
	}
	// on the OCI instances, we have at our disposal only the newest k8s-env binary and
	// its name is identical to the k8s-environment, i.e. [minikube|k3d|kind]
	def ociK8sEnvBinaryPath = k8sEnv
	return ociK8sEnvBinaryPath
}

def getJobBadge(String k8sEnv, String k8sVersion = '', String nodesCount = '', String ipFamily = '') {
	def jobBadge = k8sEnv
	if (k8sVersion) {
		jobBadge += "_$k8sVersion"
	}
	if (nodesCount) {
		jobBadge += "-${nodesCount}_nodes"
	}
	if (ipFamily && (ipFamily != "ipv4")) {
		jobBadge += "-$ipFamily"
	}
	return jobBadge.replaceAll(/[.:\/]/, '_')
}

// function returns a tuple [executionInstanceLabel, executionInstanceCount, clustersPerInstance, nodesPerCluster]
def getExecutionParams(String k8sEnv, String preferredClustersPerInstance, String nodesPerCluster) {
	if (isLocalExecutionEnvironment() || !canRunOnOci(k8sEnv)) {
		def localInstanceLabel = 'operator-ci'
		def localInstanceCount = 1
		return [
			localInstanceLabel,
			localInstanceCount,
			preferredClustersPerInstance,
			nodesPerCluster
		]
	}

	// by default we assume, the number of nodes is equal to the number of cores;
	// in the test suite we have tests lasting for long e.g. Cluster[1|3]Defaults or quite short like all
	// tests from cluster_badspec_t.py
	// at each run, we shuffle tests into random groups, so a worker may get a few short tests, or be
	// unlucky and get a few quite a long, therefore we spread tests among more OCI instances that we have
	// at disposal actually to saturate available power (e.g. run 14 instances while having only 10 VMs)
	// the more fine-grained the tests are the better for equal distribution, sooner or later the big test
	// cases like the mentioned Cluster[1|3]Defaults should be split into smaller ones
	//
	// by default agent limit for all templates (e.g. Shell_VM_8core_OL9_IAD) is 4
	// for Shell_VM_1core_OL9_IAD and Shell_VM_8core_OL9_IAD we have custom limit 10
	// see eventum tickets for more details:
	// #79571 k8s-operator: new shell VMs with OL9
	// #79871 k8s-operator: higher limit of agents for Shell VM
	//
	// we have at disposal the following machines:
	// Shell_VM_1core_OL9_IAD, 1 core, 8GB RAM, agents limit 10
	// Shell_VM_2core_OL9_IAD, 2 cores, 16GB RAM, agents limit 10
	// Shell_VM_8core_OL9_IAD, 8 cores, 32GB RAM, agents limit 4
	//
	// OCI VM: ['nodes count'] => ['agent template label', 'count of instances']
	def nodesToVM = [
		'1': ['Shell_VM_1core_OL9_IAD', 14],
		'2': ['Shell_VM_2core_OL9_IAD', 14],
		'8': ['Shell_VM_8core_OL9_IAD', 8]
	]
	def (ociInstanceLabel, ociInstanceCount) = nodesToVM[nodesPerCluster]

	def clustersPerOciInstance = '1'
	return [
		ociInstanceLabel,
		ociInstanceCount,
		clustersPerOciInstance,
		nodesPerCluster
	]
}

def generateTestSuiteSubsets(int executionInstanceCount, String jobBadge) {
	sh "mkdir -p ${env.INSTANCES_DIR}"

	def testSuiteBaseDir = env.TESTS_DIR
	def subsetCount = executionInstanceCount
	def outputDir = env.INSTANCES_DIR
	def subsetFilePrefix = "$jobBadge-${env.INSTANCE_TESTSUITE_INFIX}"
	def testSuiteSubsets = \
		sh script: "cd $testSuiteBaseDir && python3 -c \"from utils.testsuite import generate_test_suite_subsets; " + \
		"print(generate_test_suite_subsets('$testSuiteBaseDir', $subsetCount, '$outputDir', '$subsetFilePrefix'))\"", \
		returnStdout: true

	echo "number of generated test suite subsets: $testSuiteSubsets"
	listFilesInSubdir(INSTANCES_SUBDIR)
	return testSuiteSubsets.toInteger()
}

def getInstanceTestSuitePath(String jobBadge, int instanceIndex) {
	def instanceIndexPadded = instanceIndex.toString().padLeft(2, '0')
	return "${env.INSTANCES_DIR}/${jobBadge}-${env.INSTANCE_TESTSUITE_INFIX}-${instanceIndexPadded}.txt"
}

def prepareInstanceTestSuite(String jobBadge, int instanceIndex) {
	def instanceTestSuitePath = getInstanceTestSuitePath(jobBadge, instanceIndex)
	def instanceTestSuite = ""
	if (fileExists(instanceTestSuitePath)) {
		instanceTestSuite = readFile(instanceTestSuitePath)
	}
	echo "$instanceTestSuitePath:\n$instanceTestSuite"
	return sh(script: "bzip2 -9 -c $instanceTestSuitePath | base64", returnStdout: true)
}

def delayLocalJob(int interval) {
	if (isLocalExecutionEnvironment()) {
		sleep interval
	}
}

def addTestResults(String k8s_env, int expectedResultsCount) {
	countFilesInSubdir(env.LOG_SUBDIR)
	def testResultsPattern = "$k8s_env-*-result.tar.bz2"
	def testResults = findFiles glob: "**/${env.LOG_SUBDIR}/$testResultsPattern"
	if (testResults.length == 0) {
		return env.TEST_RESULTS_UNAVAILABLE
	}

	def resultPattern = "${env.LOG_DIR}/$testResultsPattern"
	sh "cat $resultPattern | tar jxf - -i -C ${env.LOG_DIR} && rm $resultPattern"

	// uncomment during Jenkins refactorings when some jobs are intentionally skipped
	// sh "touch ${env.LOG_DIR}/xml/*.xml"

	def summary = junit allowEmptyResults: true, testResults: "${env.LOG_SUBDIR}/xml/*$k8s_env-*.xml"
	echo "${summary.totalCount} tests, ${summary.passCount} passed, ${summary.failCount} failed, ${summary.skipCount} skipped"

	if (summary.totalCount > 0) {
		if (testResults.length == expectedResultsCount) {
			return env.TEST_RESULTS_ALL_AVAILABLE
		}
		return env.TEST_RESULTS_SOME_AVAILABLE
	}
	return env.TEST_RESULTS_UNAVAILABLE
}

def getMergedReports(String reportPattern) {
	def reports = findFiles glob: "**/${env.LOG_SUBDIR}/$reportPattern"
	if (reports.length == 0) {
		return ""
	}

	def reportSummaryRaw = ''
	reports.each { reportFile ->
		def report = readFile(file: reportFile.path)
		reportSummaryRaw += report.trim() + '\n'
	}

	def reportSummary = reportSummaryRaw.split('\n').sort().join('\n')
	return reportSummary
}

def storeTestSuiteMergedSubReport(String contents, String suffix) {
	if (!contents) {
		return null
	}
	def reportPath = "${env.LOG_DIR}/test_suite_${suffix}.txt"
	writeFile file: reportPath, text: contents
	echo "$reportPath:\n$contents"
	return reportPath
}

def getMergedStatsReports() {
	def statsPattern = "*-build-*-stats.log"
	def rawStats = getMergedReports(statsPattern)
	def rawStatsPath = storeTestSuiteMergedSubReport(rawStats, 'stats_raw')
	def stats = null
	if (rawStatsPath) {
		def mergeStatsScript = "${env.CI_SUBDIR}/pipeline/auxiliary/merge_test_suite_stats.py"
		stats = sh script: "cat $rawStatsPath | python3 $mergeStatsScript", returnStdout: true
		storeTestSuiteMergedSubReport(stats, 'stats')
	}
	return stats
}

def getTestSuiteReport() {
	countFilesInSubdir(env.LOG_SUBDIR)
	def testSuiteReportPattern = 'test_suite_report_*.tar.bz2'
	def testSuiteReportFiles = findFiles glob: "**/${env.LOG_SUBDIR}/$testSuiteReportPattern"
	if (testSuiteReportFiles.length == 0) {
		return ""
	}

	def reportPattern = "${env.LOG_DIR}/$testSuiteReportPattern"
	sh "cat $reportPattern | tar jxf - -i -C ${env.LOG_DIR} && rm $reportPattern"

	def reportPath = "${env.LOG_DIR}/test_suite_report.txt"
	def reportExists = fileExists reportPath
	if (!reportExists) {
		return ""
	}

	testSuiteReport = readFile(file: reportPath)
	return testSuiteReport
}

def parseTestSuiteReport() {
	def reportPath = "${env.LOG_DIR}/test_suite_report.txt"
	def reportExists = fileExists reportPath
	if (!reportExists) {
		echo "${reportPath} does not exist!"
		return
	}

	def splitReportScript = "${env.CI_SUBDIR}/pipeline/auxiliary/split_test_suite_report.sh"
	sh "$splitReportScript $reportPath"
}

def getTestSuitePartPath(String tsPart) {
	switch(tsPart) {
		case env.REPORT_PART_SUMMARY:
			return "${env.LOG_DIR}/test_suite_report_summary.txt"

		case env.REPORT_PART_FAILURES:
			return "${env.LOG_DIR}/test_suite_report_failures.txt"

		case env.REPORT_PART_SKIPPED:
			return "${env.LOG_DIR}/test_suite_report_skipped.txt"

		default:
			return "unknown test suite report part"
	}
}

def hasTestSuiteReportPart(String tsPart) {
	def testSuiteReportPartExists = fileExists getTestSuitePartPath(tsPart)
	return testSuiteReportPartExists
}

def getTestSuiteReportPart(String tsPart) {
	def reportPartPath = getTestSuitePartPath(tsPart)
	return readFile(file: reportPartPath)
}

def hasTestSuiteSummary() {
	return hasTestSuiteReportPart(env.REPORT_PART_SUMMARY)
}

def getTestSuiteSummary() {
	testSuiteSummary = "Test suite (<${env.RUN_TESTS_DISPLAY_URL}|Open>)\n"

	testSuiteSummary += getTestSuiteReportPart(env.REPORT_PART_SUMMARY)

	return testSuiteSummary
}

def hasTestSuiteFailures() {
	return hasTestSuiteReportPart(env.REPORT_PART_FAILURES)
}

def getTestSuiteFailures() {
	return getTestSuiteReportPart(env.REPORT_PART_FAILURES)
}

def hasTestSuiteSkipped() {
	return hasTestSuiteReportPart(env.REPORT_PART_SKIPPED)
}

def getTestSuiteSkipped() {
	return getTestSuiteReportPart(env.REPORT_PART_SKIPPED)
}

def anyResultsAvailable() {
	return env.MINIKUBE_RESULT_STATUS == env.TEST_RESULTS_SOME_AVAILABLE ||
		env.MINIKUBE_RESULT_STATUS == env.TEST_RESULTS_ALL_AVAILABLE ||
		env.K3D_RESULT_STATUS == env.TEST_RESULTS_SOME_AVAILABLE ||
		env.K3D_RESULT_STATUS == env.TEST_RESULTS_ALL_AVAILABLE ||
		env.KIND_RESULT_STATUS == env.TEST_RESULTS_SOME_AVAILABLE ||
		env.KIND_RESULT_STATUS == env.TEST_RESULTS_ALL_AVAILABLE
}

def getMergedIssuesReports() {
	issuesPattern = "*-build-*-issues.log"
	issues = getMergedReports(issuesPattern)
	storeTestSuiteMergedSubReport(issues, 'issues')
	return issues
}

def getTestsSuiteIssuesByEnv(String k8s_env, String result) {
	switch(result) {
		case env.TEST_RESULTS_ALL_AVAILABLE:
			return ""
		case env.TEST_RESULTS_SOME_AVAILABLE:
			return "Some test results for ${k8s_env} unavailable!\n"
		default:
			return "No test results for ${k8s_env}!\n"
	}
}

def getTestsSuiteIssues(boolean kindEnabled) {
	def testSuiteIssues = ""
	if (anyResultsAvailable()) {
		testSuiteIssues += getTestsSuiteIssuesByEnv("minikube", env.MINIKUBE_RESULT_STATUS)
		testSuiteIssues += getTestsSuiteIssuesByEnv("k3d", env.K3D_RESULT_STATUS)
		if (kindEnabled) {
			testSuiteIssues += getTestsSuiteIssuesByEnv("kind", env.KIND_RESULT_STATUS)
		}
	} else {
		testSuiteIssues = "No test results available!\n"
	}

	testSuiteIssues += getMergedIssuesReports()

	if (testSuiteIssues) {
		testSuiteIssues = "Issues:\n$testSuiteIssues"
	}

	echo testSuiteIssues

	return testSuiteIssues
}

def getBuildDuration() {
	return "${currentBuild.durationString.minus(' and counting')}"
}

def getChangeLog() {
	def changeSets = currentBuild.changeSets
	def changeSetsCount = changeSets.size()
	if (!changeSetsCount) {
		return "No changes\n"
	}

	def changesLog = "Changes (<${env.RUN_CHANGES_DISPLAY_URL}|Open>)\n"

	def allChangesCount = 0
	for (int i = 0; i < changeSetsCount; ++i) {
		allChangesCount += changeSets[i].items.length
	}

	def listOfChanges = []

	def ChangesMaxCount = 15
	def firstChangeIndex = 0
	if (allChangesCount > ChangesMaxCount) {
		listOfChanges.add("[...previous...]")
		firstChangeIndex = allChangesCount - ChangesMaxCount
	}

	def changeIndex = 0
	for (int i = 0; i < changeSetsCount; ++i) {
		def entries = changeSets[i].items
		for (int j = 0; j < entries.length; ++j) {
			if (changeIndex >= firstChangeIndex) {
				def entry = entries[j]
				listOfChanges.add("${entry.msg} [${entry.author}, ${new Date(entry.timestamp)}]")
			}
			++changeIndex
		}
	}

	changesLog += listOfChanges.reverse().join("\n")
	return changesLog
}

@NonCPS
def modifyBuildStatus(String status) {
	if (!env.BUILD_STATUS) {
		env.BUILD_STATUS = status
	} else if (!env.BUILD_STATUS.contains(status)) {
		env.BUILD_STATUS += ", " + status
	}
}

def getTestSuiteResult() {
	if (!env.INIT_STAGE_SUCCEEDED) {
		return "Init (local registry) stage failed!"
	}

	if (params.OPERATOR_BUILD_IMAGES && !env.BUILD_STAGE_SUCCEEDED) {
		return "Build dev-images stage failed!"
	}

	def testsSuiteIssues = "${env.TESTS_SUITE_ISSUES ? env.TESTS_SUITE_ISSUES + '\n' : ''}"

	if (hasTestSuiteSummary()) {
		return testsSuiteIssues + getTestSuiteSummary()
	}

	return testsSuiteIssues + "Test stage failed!"
}

def getBuildResultColor() {
	buildStatus = env.BUILD_STATUS
	if (buildStatus.contains("failure") || buildStatus.contains("aborted")) {
		return "danger"
	}

	if (buildStatus.contains("unstable") || buildStatus.contains("regression") || buildStatus.contains("unsuccessful")) {
		return "warning"
	}

	if (buildStatus.contains("fixed") || buildStatus.contains("success")) {
		return "good"
	}

	// theoretically, that point of code shouldn't be reached, but just in case return 'good' by default
	return "good"
}

def getBuildSummaryHeader() {
	return """${env.BUILD_NOTIFICATION_HEADER}
Status: ${env.BUILD_STATUS}
Duration: ${env.BUILD_DURATION}"""
}

def getBuildStats() {
	def stats = getMergedStatsReports()
	if (!stats) {
		stats = "Not found!"
	}
	return "Stats\n${stats}"
}

def getBuildSummary() {
	parseTestSuiteReport()

	def summaryColor = getBuildResultColor()

	def attachments = [
		[
			text: getBuildSummaryHeader(),
			color: summaryColor
		],
		[
			text: "${env.CHANGE_LOG}",
			color: summaryColor
		],
		[
			text: getTestSuiteResult(),
			color: summaryColor
		],
	]

	if (hasTestSuiteFailures()) {
		attachments.add([
			text: getTestSuiteFailures(),
			color: summaryColor
		])
	}

	if (hasTestSuiteSkipped()) {
		attachments.add([
			text: getTestSuiteSkipped(),
			color: summaryColor
		])
	}

	attachments.add([
		text: getBuildStats(),
		color: summaryColor
	])

	return attachments
}

def archiveArtifact() {
	listFilesInSubdir(env.LOG_SUBDIR)
	if (anyResultsAvailable()) {
		sh "cd ${env.LOG_DIR} && tar cjf ${ARTIFACT_PATH} *"
		archiveArtifacts artifacts: "${ARTIFACT_FILENAME}", fingerprint: true
	}
}

def pruneOldBuilds() {
	sh "find ${WORKSPACE}/ -maxdepth 1 -type d -name 'build-*' -mtime +30 -exec rm -rf {} \\;"
	sh "find ${WORKSPACE}/ -maxdepth 1 -type f -name '${JOB_BASE_NAME}-*-result.tar.bz2' -mtime +30 -delete"
}

return this
