# Happens to be the same as .Chart.Name
{{- define "mysql-operator.defaultDeploymentName" -}}
mysql-operator
{{- end -}}

{{- define "mysql-operator.topologyAnnotationKey" -}}
mysql.oracle.com/operator-topology
{{- end -}}

{{- define "mysql-operator.releaseAction" -}}
{{- if .Release.IsUpgrade -}}upgrade{{- else -}}install{{- end -}}
{{- end -}}

{{- define "mysql-operator.requestedDeploymentName" -}}
{{- $deploymentValues := default (dict) .Values.deployment -}}
{{- default "" $deploymentValues.name -}}
{{- end -}}

{{/*
Resolve the currently installed operator Deployment name for this Helm release.
This intentionally relies only on Helm ownership annotations so upgrades from
older chart versions continue to work even if newer helper-derived labels or
names did not exist yet.
*/}}
{{- define "mysql-operator.discoveredOwnedDeploymentNames" -}}
{{- $names := list -}}
{{- $disableLookups := .Values.disableLookups | default false -}}
{{- $releaseName := .Release.Name -}}
{{- $releaseNamespace := .Release.Namespace -}}
{{- if not $disableLookups -}}
{{- $deployments := lookup "apps/v1" "Deployment" $releaseNamespace "" -}}
{{- if $deployments -}}
{{- range $deployment := (default (list) $deployments.items) -}}
{{- $metadata := default (dict) $deployment.metadata -}}
{{- $annotations := default (dict) $metadata.annotations -}}
{{- if and
      $metadata.name
      (eq (default "" (index $annotations "meta.helm.sh/release-name")) $releaseName)
      (eq (default "" (index $annotations "meta.helm.sh/release-namespace")) $releaseNamespace) -}}
{{- $names = append $names $metadata.name -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- toYaml $names -}}
{{- end -}}

{{- define "mysql-operator.discoveredDeploymentName" -}}
{{- $names := include "mysql-operator.discoveredOwnedDeploymentNames" . | fromYamlArray -}}
{{- if gt (len $names) 1 -}}
{{- fail (printf "Unable to determine the current operator deployment name for Helm release %q in namespace %q: found multiple owned Deployments: %s" .Release.Name .Release.Namespace (($names | sortAlpha) | join ", ")) -}}
{{- end -}}
{{- if eq (len $names) 1 -}}
{{- index $names 0 -}}
{{- end -}}
{{- end -}}

{{/*
Resolve the currently installed operator Deployment selector labels for this
Helm release. Upgrades must reuse the live selector exactly because Kubernetes
Deployment selectors are immutable.
*/}}
{{- define "mysql-operator.discoveredDeploymentSelectorLabels" -}}
{{- $disableLookups := .Values.disableLookups | default false -}}
{{- if and .Release.IsUpgrade (not $disableLookups) -}}
{{- $deploymentName := include "mysql-operator.discoveredDeploymentName" . | trim -}}
{{- if $deploymentName -}}
{{- $deployment := lookup "apps/v1" "Deployment" .Release.Namespace $deploymentName -}}
{{- if $deployment -}}
{{- $selector := default (dict) (default (dict) $deployment.spec).selector -}}
{{- $matchLabels := default (dict) $selector.matchLabels -}}
{{- toYaml $matchLabels -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.deploymentName" -}}
{{- $default_deploy_name := include "mysql-operator.defaultDeploymentName" . -}}
{{- $requested_deploy_name := include "mysql-operator.requestedDeploymentName" . -}}
{{- if $requested_deploy_name -}}
{{- $requested_deploy_name -}}
{{- else if .Release.IsUpgrade -}}
{{- $discovered_deploy_name := include "mysql-operator.discoveredDeploymentName" . -}}
{{- if $discovered_deploy_name -}}
{{- $discovered_deploy_name -}}
{{- else -}}
{{- $default_deploy_name -}}
{{- end -}}
{{- else -}}
{{- $default_deploy_name -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.checkDeploymentNameImmutable" -}}
{{- $disableLookups := .Values.disableLookups | default false -}}
{{- if and .Release.IsUpgrade (not $disableLookups) -}}
{{- $requested_deploy_name := include "mysql-operator.requestedDeploymentName" . -}}
{{- if $requested_deploy_name -}}
{{- $discovered_deploy_name := include "mysql-operator.discoveredDeploymentName" . -}}
{{- if and $discovered_deploy_name (ne $requested_deploy_name $discovered_deploy_name) -}}
{{- fail (printf "Invalid values: deployment.name is immutable after install because it determines persistent RBAC and peering identity. This release currently uses deployment %q, but the upgrade requested %q." $discovered_deploy_name $requested_deploy_name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.prefixName" -}}
{{- $default_deploy_name := include "mysql-operator.defaultDeploymentName" . -}}
{{- $deployment := include "mysql-operator.deploymentName" . -}}
{{- if eq $deployment $default_deploy_name -}}
mysql
{{- else -}}
{{- printf "%s-%s" .Release.Namespace $deployment -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.globalInstanceName" -}}
{{ include "mysql-operator.prefixName" . }}-operator
{{- end -}}

# ClusterPeeringName is global, not namespaced. This is why prefixName is used, which might use .Release.Namespace
# when deployment name is specified and it is not mysql-operator (for BC), which is the default one. Same as for globalInstanceName.
# The default deployment name intentionally preserves the legacy fixed global identity for backwards compatibility.
# Only one cluster-wide non-standalone operator is supported per cluster. OTOH, peeringName is used when multiple
# namespaces (thus no clusterwide) are used and in every namespace a peering object named after the contents of peeringName.
{{- define "mysql-operator.clusterPeeringName" -}}
{{- include "mysql-operator.prefixName" . }}-operator
{{- end -}}

# Peering name is namespaced. Thus it doesn't include the name of the namespace but only the deployment name
{{- define "mysql-operator.peeringName" -}}
{{- include "mysql-operator.deploymentName" . -}}
{{- end -}}

{{- define "mysql-operator.envVarPeeringName" -}}
{{- $clusterwide := include "mysql-operator.clusterwide" . | int -}}
{{- if $clusterwide -}}
{{- include "mysql-operator.clusterPeeringName" . -}}
{{- else -}}
{{- include "mysql-operator.peeringName" . -}}
{{- end -}}
{{- end -}}

{{/*
Keep custom deployment names short enough for the derived 63-char Service name
and app.kubernetes.io/instance label value:
- <deployment> for the Service and namespaced KopfPeering
- <namespace>-<deployment>-operator for the instance label value
Other derived object names use Kubernetes metadata.name and therefore are not
the limiting 63-char constraint here.
*/}}
{{- define "mysql-operator.k8sNameLimit" -}}
63
{{- end -}}

{{- define "mysql-operator.maxCustomDeploymentNameLength" -}}
{{- $nameLimit := include "mysql-operator.k8sNameLimit" . | int -}}
{{- $namespaceLen := len .Release.Namespace -}}
{{- $maxLen := min $nameLimit (sub $nameLimit (add $namespaceLen 1 (len "-operator"))) -}}
{{- if lt $maxLen 0 -}}0{{- else -}}{{ $maxLen }}{{- end -}}
{{- end -}}

{{- define "mysql-operator.checkDeploymentNameLength" -}}
{{- $defaultName := include "mysql-operator.defaultDeploymentName" . -}}
{{- $deploymentName := include "mysql-operator.deploymentName" . -}}
{{- if ne $deploymentName $defaultName -}}
{{- $nameLimit := include "mysql-operator.k8sNameLimit" . | int -}}
{{- $maxLen := include "mysql-operator.maxCustomDeploymentNameLength" . | int -}}
{{- $actualLen := len $deploymentName -}}
{{- if gt $actualLen $maxLen -}}
{{- fail (printf "Invalid values: deployment.name %q is %d characters long, but at most %d are allowed in namespace %q to keep derived Kubernetes names and labels within %d characters." $deploymentName $actualLen $maxLen .Release.Namespace $nameLimit) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.failIfClusterScopedResourceExists" -}}
{{- $existing := lookup .apiVersion .kind "" .name -}}
{{- if $existing -}}
{{- fail (printf "Invalid install: %s %q already exists and conflicts with the cluster-scoped identity derived from deployment.name %q in namespace %q. Clean up the existing operator or choose a unique deployment.name." .kind .name .deploymentName .releaseNamespace) -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.checkClusterScopedIdentityConflicts" -}}
{{- $disableLookups := .Values.disableLookups | default false -}}
{{- $standalone := include "mysql-operator.deployment.standalone" . | trim | eq "true" -}}
{{- $clusterwide := include "mysql-operator.clusterwide" . | int -}}
{{- if and .Release.IsInstall (not $disableLookups) -}}
{{- include "mysql-operator.failIfClusterScopedResourceExists" (dict
      "apiVersion" "rbac.authorization.k8s.io/v1"
      "kind" "ClusterRole"
      "name" (include "mysql-operator.operatorRoleName" .)
      "deploymentName" (include "mysql-operator.deploymentName" .)
      "releaseNamespace" .Release.Namespace) -}}
{{- include "mysql-operator.failIfClusterScopedResourceExists" (dict
      "apiVersion" "rbac.authorization.k8s.io/v1"
      "kind" "ClusterRole"
      "name" (include "mysql-operator.sidecarRoleName" .)
      "deploymentName" (include "mysql-operator.deploymentName" .)
      "releaseNamespace" .Release.Namespace) -}}
{{- include "mysql-operator.failIfClusterScopedResourceExists" (dict
      "apiVersion" "rbac.authorization.k8s.io/v1"
      "kind" "ClusterRole"
      "name" (include "mysql-operator.switchoverRoleName" .)
      "deploymentName" (include "mysql-operator.deploymentName" .)
      "releaseNamespace" .Release.Namespace) -}}
{{- include "mysql-operator.failIfClusterScopedResourceExists" (dict
      "apiVersion" "rbac.authorization.k8s.io/v1"
      "kind" "ClusterRoleBinding"
      "name" (include "mysql-operator.operatorRoleBindingName" .)
      "deploymentName" (include "mysql-operator.deploymentName" .)
      "releaseNamespace" .Release.Namespace) -}}
{{- if and (not $standalone) (eq $clusterwide 1) -}}
{{- include "mysql-operator.failIfClusterScopedResourceExists" (dict
      "apiVersion" "zalando.org/v1"
      "kind" "ClusterKopfPeering"
      "name" (include "mysql-operator.clusterPeeringName" .)
      "deploymentName" (include "mysql-operator.deploymentName" .)
      "releaseNamespace" .Release.Namespace) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.operatorContainerExists" -}}
{{- $deployment := .deployment -}}
{{- $podSpec := default (dict) (default (dict) (default (dict) $deployment.spec).template).spec -}}
{{- $found := false -}}
{{- range $container := (default (list) $podSpec.containers) -}}
  {{- if eq (default "" $container.name) "mysql-operator" -}}
    {{- $found = true -}}
  {{- end -}}
{{- end -}}
{{- if $found -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "mysql-operator.operatorContainerEnvMap" -}}
{{- $deployment := .deployment -}}
{{- $envMap := dict -}}
{{- $podSpec := default (dict) (default (dict) (default (dict) $deployment.spec).template).spec -}}
{{- range $container := (default (list) $podSpec.containers) -}}
  {{- if eq (default "" $container.name) "mysql-operator" -}}
    {{- range $env := (default (list) $container.env) -}}
      {{- if $env.name -}}
        {{- $_ := set $envMap $env.name (default "" $env.value) -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- toYaml $envMap -}}
{{- end -}}

{{- define "mysql-operator.deploymentRef" -}}
{{- $deployment := .deployment -}}
{{- $metadata := default (dict) $deployment.metadata -}}
{{- printf "%s/%s" (default "" $metadata.namespace) (default "" $metadata.name) -}}
{{- end -}}

{{- define "mysql-operator.isLegacyGlobalOperatorDeployment" -}}
{{- $root := .root -}}
{{- $deployment := .deployment -}}
{{- $defaultDeploymentName := include "mysql-operator.defaultDeploymentName" $root -}}
{{- if ne (include "mysql-operator.operatorContainerExists" (dict "deployment" $deployment) | trim) "true" -}}
false
{{- else -}}
{{- $metadata := default (dict) $deployment.metadata -}}
{{- $envs := include "mysql-operator.operatorContainerEnvMap" (dict "deployment" $deployment) | fromYaml | default (dict) -}}
{{- $selector := default (dict) (default (dict) $deployment.spec).selector -}}
{{- $selectorLabels := default (dict) $selector.matchLabels -}}
{{- $templateMetadata := default (dict) (default (dict) (default (dict) $deployment.spec).template).metadata -}}
{{- $templateLabels := default (dict) $templateMetadata.labels -}}
{{- $hasLegacySelector := and (eq (len $selectorLabels) 1) (eq (default "" (index $selectorLabels "name")) $defaultDeploymentName) -}}
{{- $hasLegacyTemplate := and (eq (len $templateLabels) 1) (eq (default "" (index $templateLabels "name")) $defaultDeploymentName) -}}
{{- if and
      (eq (default "" $metadata.name) $defaultDeploymentName)
      (not (hasKey $envs "OPERATOR_NAMESPACES"))
      (not (hasKey $envs "OPERATOR_STANDALONE"))
      $hasLegacySelector
      $hasLegacyTemplate -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.isRecognizedOperatorDeployment" -}}
{{- $root := .root -}}
{{- $deployment := .deployment -}}
{{- $metadata := default (dict) $deployment.metadata -}}
{{- $labels := default (dict) $metadata.labels -}}
{{- $annotations := default (dict) $metadata.annotations -}}
{{- $annotationKey := include "mysql-operator.topologyAnnotationKey" $root -}}
{{- $defaultDeploymentName := include "mysql-operator.defaultDeploymentName" $root -}}
{{- $hasOperatorContainer := eq (include "mysql-operator.operatorContainerExists" (dict "deployment" $deployment) | trim) "true" -}}
{{- $hasPersistedTopology := hasKey $annotations $annotationKey -}}
{{- $hasManagedOperatorLabels := and
      (eq (default "" (index $labels "app.kubernetes.io/name")) $defaultDeploymentName)
      (eq (default "" (index $labels "app.kubernetes.io/component")) "controller") -}}
{{- $hasLegacyDefaultIdentity := eq (include "mysql-operator.isLegacyGlobalOperatorDeployment" (dict "root" $root "deployment" $deployment) | trim) "true" -}}
{{- $isMysqlOperator := and
      $hasOperatorContainer
      (or $hasPersistedTopology $hasManagedOperatorLabels $hasLegacyDefaultIdentity) -}}
{{- if $isMysqlOperator -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "mysql-operator.isOwnedDeploymentForCurrentRelease" -}}
{{- $root := .root -}}
{{- $deployment := .deployment -}}
{{- $metadata := default (dict) $deployment.metadata -}}
{{- $annotations := default (dict) $metadata.annotations -}}
{{- if and
      (eq (default "" (index $annotations "meta.helm.sh/release-name")) $root.Release.Name)
      (eq (default "" (index $annotations "meta.helm.sh/release-namespace")) $root.Release.Namespace) -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{- define "mysql-operator.resolvedOperatorTopologyFromDeployment" -}}
{{- $root := .root -}}
{{- $deployment := .deployment -}}
{{- $ref := default (include "mysql-operator.deploymentRef" (dict "deployment" $deployment)) .ref -}}
{{- if eq (include "mysql-operator.isRecognizedOperatorDeployment" (dict "root" $root "deployment" $deployment) | trim) "true" -}}
{{- $annotationKey := include "mysql-operator.topologyAnnotationKey" $root -}}
{{- $metadata := default (dict) $deployment.metadata -}}
{{- $annotations := default (dict) $metadata.annotations -}}
{{- if hasKey $annotations $annotationKey -}}
{{- include "mysql-operator.topologyFromAnnotationValue" (dict "root" $root "ref" $ref "annotation" (index $annotations $annotationKey)) -}}
{{- else -}}
{{- $envs := include "mysql-operator.operatorContainerEnvMap" (dict "deployment" $deployment) | fromYaml | default (dict) -}}
{{- $envTopologyText := include "mysql-operator.operatorTopologyFromEnvMap" (dict "root" $root "ref" $ref "envs" $envs) | trim -}}
{{- if $envTopologyText -}}
  {{- $envTopology := $envTopologyText | fromYaml | default (dict) -}}
  {{- if and
        (eq (default "" (index $envTopology "scope")) "global")
        (not (default false (index $envTopology "standalone"))) -}}
{{- $envTopologyText -}}
  {{- else -}}
{{- fail (printf "Recognized operator deployment %s is missing %s and cannot bootstrap persisted topology for scoped or standalone configurations." $ref $annotationKey) -}}
  {{- end -}}
{{- else if eq (include "mysql-operator.isLegacyGlobalOperatorDeployment" (dict "root" $root "deployment" $deployment) | trim) "true" -}}
{{- include "mysql-operator.topologyFromParts" (dict "namespaces" (list) "standalone" false) -}}
{{- else -}}
{{- fail (printf "Recognized operator deployment %s is missing %s and does not match a supported legacy global topology." $ref $annotationKey) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.checkTopologyImmutable" -}}
{{- $disableLookups := .Values.disableLookups | default false -}}
{{- if and .Release.IsUpgrade (not $disableLookups) -}}
{{- $deploymentName := include "mysql-operator.discoveredDeploymentName" . | trim -}}
{{- if $deploymentName -}}
{{- $deployment := lookup "apps/v1" "Deployment" .Release.Namespace $deploymentName -}}
{{- if not $deployment -}}
{{- fail (printf "Unable to resolve the current operator topology because Deployment %s/%s was not found." .Release.Namespace $deploymentName) -}}
{{- end -}}
{{- $ref := printf "%s/%s" .Release.Namespace $deploymentName -}}
{{- $currentTopologyText := include "mysql-operator.resolvedOperatorTopologyFromDeployment" (dict "root" . "deployment" $deployment "ref" $ref) | trim -}}
{{- if not $currentTopologyText -}}
{{- fail (printf "Unable to resolve the current operator topology from Deployment %s." $ref) -}}
{{- end -}}
{{- $currentTopology := $currentTopologyText | fromYaml | default (dict) -}}
{{- $requestedTopology := include "mysql-operator.requestedTopology" . | fromYaml | default (dict) -}}
{{- if ne (include "mysql-operator.topologyEquals" (dict "lhs" $currentTopology "rhs" $requestedTopology) | trim) "true" -}}
  {{- $currentSummary := include "mysql-operator.topologySummary" (dict "topology" $currentTopology) -}}
  {{- $requestedSummary := include "mysql-operator.topologySummary" (dict "topology" $requestedTopology) -}}
  {{- $currentScope := default "" (index $currentTopology "scope") -}}
  {{- $requestedScope := default "" (index $requestedTopology "scope") -}}
  {{- $currentNamespaces := default (list) (index $currentTopology "namespaces") -}}
  {{- $requestedNamespaces := default (list) (index $requestedTopology "namespaces") -}}
  {{- $currentStandalone := default false (index $currentTopology "standalone") -}}
  {{- $requestedStandalone := default false (index $requestedTopology "standalone") -}}
  {{- if ne $currentScope $requestedScope -}}
{{- fail (printf "Invalid values: operator topology is immutable after install. Deployment %s currently uses %s, but the upgrade requested %s. Global operators may only upgrade as global, and scoped operators may not change scope in this release." $ref $currentSummary $requestedSummary) -}}
  {{- end -}}
  {{- if and (eq $currentScope "scoped") (ne ($currentNamespaces | join ",") ($requestedNamespaces | join ",")) -}}
{{- fail (printf "Invalid values: operator watched namespace set is immutable after install. Deployment %s currently uses %s, but the upgrade requested %s." $ref $currentSummary $requestedSummary) -}}
  {{- end -}}
  {{- if ne $currentStandalone $requestedStandalone -}}
{{- fail (printf "Invalid values: deployment.standalone is immutable after install. Deployment %s currently uses %s, but the upgrade requested %s." $ref $currentSummary $requestedSummary) -}}
  {{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.walkNamespaces" -}}
{{- $root := .root -}}
{{- $cb := .cb -}}
{{- $seen := dict -}}

{{- range $namespace := (include "mysql-operator.normalizedNamespaces" $root | fromYamlArray) -}}
  {{- if and $namespace (not (hasKey $seen $namespace)) -}}
    {{- $_ := set $seen $namespace true -}}
    {{- if $cb -}}
      {{- $rendered := include $cb (dict "root" $root "namespace" $namespace) | trim -}}
      {{- if $rendered }}
---
{{ $rendered }}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.checkNamespacesExplicitOnly" -}}
{{- $bad := list -}}
{{- range $ns := (include "mysql-operator.normalizedNamespaces" . | fromYamlArray) -}}
  {{- if or (contains "*" $ns) (contains "?" $ns) (contains "!" $ns) -}}
    {{- $bad = append $bad $ns -}}
  {{- end -}}
{{- end -}}
{{- if gt (len $bad) 0 -}}
{{- fail (printf "Invalid values: deployment.namespaces / OPERATOR_NAMESPACES supports explicit namespace names only. Remove pattern entries: %s." ($bad | join ", ")) -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.callbacks.warnMissingNamespace" -}}
{{- $disableLookups := .root.Values.disableLookups | default false -}}
{{- if not $disableLookups -}}
{{- $nsObj := lookup "v1" "Namespace" "" .namespace -}}
{{- if not $nsObj }}
============================================================================================
WARNING: Namespace '{{ .namespace }}' was not found and was implicitly created
============================================================================================
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.callbacks.warnMissingNamespaceStandalone" -}}
{{- $disableLookups := .root.Values.disableLookups | default false -}}
{{- if not $disableLookups -}}
{{- $nsObj := lookup "v1" "Namespace" "" .namespace -}}
{{- if not $nsObj }}
============================================================================================
WARNING: Namespace '{{ .namespace }}' was not found
============================================================================================
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.callbacks.createMissingNamespace" -}}
{{- $disableLookups := .root.Values.disableLookups | default false -}}
{{- if $disableLookups }}
apiVersion: v1
kind: Namespace
metadata:
  name: {{ .namespace }}
  annotations:
    # Important: Prevents Helm from deleting this namespace on helm uninstall.
    # In case the operator is installed in the same namespace where the cluster will be
    # helm takes ownership (similarly to how kopf does) and will delete the namespace on
    # operator deinstallation (even in there are clusters running, or deleted but the PVCs still exist)
    helm.sh/resource-policy: keep
{{- else -}}
{{- $nsObj := lookup "v1" "Namespace" "" .namespace -}}
{{- if not $nsObj }}
apiVersion: v1
kind: Namespace
metadata:
  name: {{ .namespace }}
  annotations:
    # Important: Prevents Helm from deleting this namespace on helm uninstall.
    # In case the operator is installed in the same namespace where the cluster will be
    # helm takes ownership (similarly to how kopf does) and will delete the namespace on
    # operator deinstallation (even in there are clusters running, or deleted but the PVCs still exist)
    helm.sh/resource-policy: keep
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.callbacks.createNamespacedKopfPeering" -}}
apiVersion: zalando.org/v1
kind: KopfPeering
metadata:
  name: {{ include "mysql-operator.peeringName" .root }}
  namespace: {{ .namespace }}
  labels:
    {{- include "mysql-operator.globalLabels" .root | nindent 4 }}
{{- end -}}

{{- define "mysql-operator.normalizedNamespaces" -}}
{{- $result := list -}}
{{- $seen := dict -}}
{{- $deploymentValues := default (dict) .Values.deployment -}}
{{- range $rawNamespace := ($deploymentValues.namespaces | default list) -}}
  {{- $namespace := trim $rawNamespace -}}
  {{- if and $namespace (not (hasKey $seen $namespace)) -}}
    {{- $_ := set $seen $namespace true -}}
    {{- $result = append $result $namespace -}}
  {{- end -}}
{{- end -}}
{{- $result | toYaml -}}
{{- end -}}

{{- define "mysql-operator.canonicalNamespaces" -}}
{{- $namespaces := include "mysql-operator.normalizedNamespaces" . | fromYamlArray | default (list) -}}
{{- toYaml ($namespaces | sortAlpha) -}}
{{- end -}}

{{- define "mysql-operator.canonicalNamespacesFromList" -}}
{{- $result := list -}}
{{- $seen := dict -}}
{{- $invalid := list -}}
{{- range $rawNamespace := (.namespaces | default list) -}}
  {{- $namespace := trim (printf "%v" $rawNamespace) -}}
  {{- if $namespace -}}
    {{- if or (contains "*" $namespace) (contains "?" $namespace) (contains "!" $namespace) -}}
      {{- if not (hasKey $seen (printf "invalid:%s" $namespace)) -}}
        {{- $_ := set $seen (printf "invalid:%s" $namespace) true -}}
        {{- $invalid = append $invalid $namespace -}}
      {{- end -}}
    {{- else if not (hasKey $seen $namespace) -}}
      {{- $_ := set $seen $namespace true -}}
      {{- $result = append $result $namespace -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- if gt (len $invalid) 0 -}}
{{- fail (printf "Invalid values: deployment.namespaces / OPERATOR_NAMESPACES supports explicit namespace names only. Remove pattern entries: %s." ($invalid | join ", ")) -}}
{{- end -}}
{{- toYaml ($result | sortAlpha) -}}
{{- end -}}

{{- define "mysql-operator.canonicalNamespacesFromCsv" -}}
{{- include "mysql-operator.canonicalNamespacesFromList" (dict "namespaces" (splitList "," (default "" .csv))) -}}
{{- end -}}

{{- define "mysql-operator.clusterwide" -}}
{{- $namespaces := (include "mysql-operator.normalizedNamespaces" . | fromYamlArray) -}}
{{- if eq (len $namespaces) 0 -}}1{{- else -}}0{{- end -}}
{{- end -}}

{{- define "mysql-operator.namespaces" -}}
{{- (include "mysql-operator.normalizedNamespaces" . | fromYamlArray) | join "," -}}
{{- end -}}

{{- define "mysql-operator.boolFromValue" -}}
{{- $value := lower (trim (printf "%v" (default "" .value))) -}}
{{- if or (eq $value "1") (eq $value "true") -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "mysql-operator.topologyFromParts" -}}
{{- $namespaces := default (list) .namespaces -}}
{{- $standalone := default false .standalone -}}
{{- $scope := ternary "global" "scoped" (eq (len $namespaces) 0) -}}
{{- toYaml (dict "version" 1 "scope" $scope "standalone" $standalone "namespaces" $namespaces) -}}
{{- end -}}

{{- define "mysql-operator.requestedTopology" -}}
{{- $namespaces := include "mysql-operator.canonicalNamespaces" . | fromYamlArray | default (list) -}}
{{- $standalone := include "mysql-operator.deployment.standalone" . | trim | eq "true" -}}
{{- include "mysql-operator.topologyFromParts" (dict "namespaces" $namespaces "standalone" $standalone) -}}
{{- end -}}

{{- define "mysql-operator.requestedTopologyAnnotationValue" -}}
{{- $topology := include "mysql-operator.requestedTopology" . | fromYaml | default (dict) -}}
{{- $scope := default "" (index $topology "scope") -}}
{{- $standalone := default false (index $topology "standalone") -}}
{{- $namespaces := default (list) (index $topology "namespaces") -}}
{{- printf "{\"version\":1,\"scope\":%s,\"standalone\":%t,\"namespaces\":%s}" ($scope | quote) $standalone ($namespaces | toJson) -}}
{{- end -}}

{{- define "mysql-operator.topologySummary" -}}
{{- $topology := default (dict) .topology -}}
{{- $scope := default "" (index $topology "scope") -}}
{{- $standalone := default false (index $topology "standalone") -}}
{{- $mode := ternary "standalone" "non-standalone" $standalone -}}
{{- if eq $scope "global" -}}
{{- printf "global %s" $mode -}}
{{- else -}}
{{- printf "scoped %s watching [%s]" $mode ((default (list) (index $topology "namespaces")) | join ", ") -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.topologyEquals" -}}
{{- $lhs := default (dict) .lhs -}}
{{- $rhs := default (dict) .rhs -}}
{{- $lhsScope := default "" (index $lhs "scope") -}}
{{- $rhsScope := default "" (index $rhs "scope") -}}
{{- $lhsStandalone := default false (index $lhs "standalone") -}}
{{- $rhsStandalone := default false (index $rhs "standalone") -}}
{{- $lhsNamespaces := default (list) (index $lhs "namespaces") -}}
{{- $rhsNamespaces := default (list) (index $rhs "namespaces") -}}
{{- if and
      (eq $lhsScope $rhsScope)
      (eq $lhsStandalone $rhsStandalone)
      (eq ($lhsNamespaces | join ",") ($rhsNamespaces | join ",")) -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{- define "mysql-operator.topologyOverlapNamespaces" -}}
{{- $lhs := default (dict) .lhs -}}
{{- $rhs := default (dict) .rhs -}}
{{- if or (eq (default "" (index $lhs "scope")) "global") (eq (default "" (index $rhs "scope")) "global") -}}
{{- toYaml (list) -}}
{{- else -}}
{{- $overlaps := list -}}
{{- $rhsSet := dict -}}
{{- range $namespace := (default (list) (index $rhs "namespaces")) -}}
  {{- $_ := set $rhsSet $namespace true -}}
{{- end -}}
{{- range $namespace := (default (list) (index $lhs "namespaces")) -}}
  {{- if hasKey $rhsSet $namespace -}}
    {{- $overlaps = append $overlaps $namespace -}}
  {{- end -}}
{{- end -}}
{{- toYaml ($overlaps | sortAlpha) -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.topologiesOverlap" -}}
{{- $lhs := default (dict) .lhs -}}
{{- $rhs := default (dict) .rhs -}}
{{- $overlaps := include "mysql-operator.topologyOverlapNamespaces" . | fromYamlArray | default (list) -}}
{{- if or (eq (default "" (index $lhs "scope")) "global") (eq (default "" (index $rhs "scope")) "global") (gt (len $overlaps) 0) -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "mysql-operator.topologyFromAnnotationValue" -}}
{{- $root := .root -}}
{{- $ref := .ref -}}
{{- $annotationKey := include "mysql-operator.topologyAnnotationKey" $root -}}
{{- $parsed := fromJson (default "" .annotation) -}}
{{- if not (kindIs "map" $parsed) -}}
{{- fail (printf "Recognized operator deployment %s has invalid %s: annotation must contain valid JSON." $ref $annotationKey) -}}
{{- end -}}
{{- $version := default 0 (index $parsed "version") | int -}}
{{- if ne $version 1 -}}
{{- fail (printf "Recognized operator deployment %s has invalid %s: version must be 1." $ref $annotationKey) -}}
{{- end -}}
{{- $scope := default "" (index $parsed "scope") -}}
{{- if and (ne $scope "global") (ne $scope "scoped") -}}
{{- fail (printf "Recognized operator deployment %s has invalid %s: scope must be global or scoped." $ref $annotationKey) -}}
{{- end -}}
{{- $standalone := index $parsed "standalone" -}}
{{- if not (kindIs "bool" $standalone) -}}
{{- fail (printf "Recognized operator deployment %s has invalid %s: standalone must be a boolean." $ref $annotationKey) -}}
{{- end -}}
{{- $namespacesRaw := default (list) (index $parsed "namespaces") -}}
{{- if not (kindIs "slice" $namespacesRaw) -}}
{{- fail (printf "Recognized operator deployment %s has invalid %s: namespaces must be an array." $ref $annotationKey) -}}
{{- end -}}
{{- $namespaces := include "mysql-operator.canonicalNamespacesFromList" (dict "namespaces" $namespacesRaw) | fromYamlArray | default (list) -}}
{{- if and (eq $scope "global") (gt (len $namespaces) 0) -}}
{{- fail (printf "Recognized operator deployment %s has invalid %s: global topology must not declare namespaces." $ref $annotationKey) -}}
{{- end -}}
{{- if and (eq $scope "scoped") (eq (len $namespaces) 0) -}}
{{- fail (printf "Recognized operator deployment %s has invalid %s: scoped topology must declare at least one namespace." $ref $annotationKey) -}}
{{- end -}}
{{- include "mysql-operator.topologyFromParts" (dict "namespaces" $namespaces "standalone" $standalone) -}}
{{- end -}}

{{- define "mysql-operator.operatorTopologyFromEnvMap" -}}
{{- $root := .root -}}
{{- $ref := .ref -}}
{{- $annotationKey := include "mysql-operator.topologyAnnotationKey" $root -}}
{{- $envs := default (dict) .envs -}}
{{- $hasStandalone := hasKey $envs "OPERATOR_STANDALONE" -}}
{{- $hasNamespaces := hasKey $envs "OPERATOR_NAMESPACES" -}}
{{- if or $hasStandalone $hasNamespaces -}}
  {{- if not (and $hasStandalone $hasNamespaces) -}}
    {{- fail (printf "Recognized operator deployment %s is missing %s and has incomplete topology env configuration." $ref $annotationKey) -}}
  {{- end -}}
  {{- $standalone := eq (include "mysql-operator.boolFromValue" (dict "value" (index $envs "OPERATOR_STANDALONE")) | trim) "true" -}}
  {{- $namespaces := include "mysql-operator.canonicalNamespacesFromCsv" (dict "csv" (index $envs "OPERATOR_NAMESPACES")) | fromYamlArray | default (list) -}}
  {{- include "mysql-operator.topologyFromParts" (dict "namespaces" $namespaces "standalone" $standalone) -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.checkNoOverlappingOperators" -}}
{{- $disableLookups := .Values.disableLookups | default false -}}
{{- if not $disableLookups -}}
{{- $requestedTopology := include "mysql-operator.requestedTopology" . | fromYaml | default (dict) -}}
{{- $requestedSummary := include "mysql-operator.topologySummary" (dict "topology" $requestedTopology) -}}
{{- $action := include "mysql-operator.releaseAction" . | trim -}}
{{- $deployments := lookup "apps/v1" "Deployment" "" "" -}}
{{- if $deployments -}}
{{- range $deployment := (default (list) $deployments.items) -}}
  {{- $ownedByCurrentRelease := and $.Release.IsUpgrade (eq (include "mysql-operator.isOwnedDeploymentForCurrentRelease" (dict "root" $ "deployment" $deployment) | trim) "true") -}}
  {{- if not $ownedByCurrentRelease -}}
    {{- $recognized := eq (include "mysql-operator.isRecognizedOperatorDeployment" (dict "root" $ "deployment" $deployment) | trim) "true" -}}
    {{- if $recognized -}}
      {{- $ref := include "mysql-operator.deploymentRef" (dict "deployment" $deployment) -}}
      {{- $existingTopologyText := include "mysql-operator.resolvedOperatorTopologyFromDeployment" (dict "root" $ "deployment" $deployment "ref" $ref) | trim -}}
      {{- if $existingTopologyText -}}
        {{- $existingTopology := $existingTopologyText | fromYaml | default (dict) -}}
        {{- if eq (include "mysql-operator.topologiesOverlap" (dict "lhs" $requestedTopology "rhs" $existingTopology) | trim) "true" -}}
          {{- $existingSummary := include "mysql-operator.topologySummary" (dict "topology" $existingTopology) -}}
          {{- $overlappingNamespaces := include "mysql-operator.topologyOverlapNamespaces" (dict "lhs" $requestedTopology "rhs" $existingTopology) | fromYamlArray | default (list) -}}
          {{- if gt (len $overlappingNamespaces) 0 -}}
{{- fail (printf "Invalid %s: requested operator topology %s overlaps existing operator %s (%s); overlapping namespaces: %s." $action $requestedSummary $ref $existingSummary ($overlappingNamespaces | join ", ")) -}}
          {{- else -}}
{{- fail (printf "Invalid %s: requested operator topology %s overlaps existing operator %s (%s); a global operator overlaps every namespace." $action $requestedSummary $ref $existingSummary) -}}
          {{- end -}}
        {{- end -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- define "mysql-operator.operatorRoleName" -}}
{{- include "mysql-operator.prefixName" . }}-operator
{{- end -}}

{{- define "mysql-operator.sidecarRoleName" -}}
{{- include "mysql-operator.prefixName" . }}-sidecar
{{- end -}}

{{- define "mysql-operator.switchoverRoleName" -}}
{{ include "mysql-operator.prefixName" . }}-switchover
{{- end -}}
{{- define "mysql-operator.operatorRoleBindingName" -}}
{{- include "mysql-operator.prefixName" . }}-operator-rolebinding
{{- end -}}

{{- define "mysql-operator.operatorSAName" -}}
{{- include "mysql-operator.prefixName" . }}-operator-sa
{{- end -}}

{{- define "mysql-operator.checkNotDefaultNamespace" -}}
{{- if and (.Release.IsInstall) (eq .Release.Namespace "default") -}}
  {{- fail "Please provide a namespace with -n/--namespace . The operator cannot be installed in the 'default' namespace" -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.deployment.standalone" -}}
{{- $deploymentValues := default (dict) .Values.deployment -}}
{{- if (($deploymentValues.standalone) | default false) -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{- define "mysql-operator.deployment.replicas" -}}
{{- $replicas := int (default 1 .Values.replicas) -}}
{{- $standalone := (include "mysql-operator.deployment.standalone" . | trim | eq "true") -}}
{{- if and $standalone (gt $replicas 1) -}}
{{- fail (printf "Invalid values: deployment.standalone=true requires replicas=1 (got %d)" $replicas) -}}
{{- end -}}
{{- $replicas -}}
{{- end -}}

{{- define "mysql-operator.deployment.strategy" -}}
{{- $deploymentValues := default (dict) .Values.deployment -}}
{{- if $deploymentValues.strategy -}}
strategy:
{{- toYaml $deploymentValues.strategy | nindent 2 }}
{{- else if (include "mysql-operator.deployment.standalone" . | trim | eq "true") -}}
strategy:
  type: Recreate
{{- end -}}
{{- end -}}

{{- define "mysql-operator.resolveDeprecatedDeploymentMap" -}}
{{- $root := .root -}}
{{- $oldKey := .oldKey -}}
{{- $newKey := .newKey -}}
{{- $deploymentValues := default (dict) $root.Values.deployment -}}
{{- $newValue := index $deploymentValues $newKey -}}
{{- $oldValue := index $root.Values $oldKey -}}
{{- $newSet := not (empty $newValue) -}}
{{- $oldSet := not (empty $oldValue) -}}
{{- if and $oldSet $newSet -}}
{{- fail (printf "Invalid values: %s and deployment.%s cannot both be set" $oldKey $newKey) -}}
{{- end -}}
{{- $warning := "" -}}
{{- if $oldSet -}}
{{- $warning = printf "WARNING: %s is deprecated; use deployment.%s instead." $oldKey $newKey -}}
{{- end -}}
{{- $value := dict -}}
{{- if $newSet -}}
{{- $value = $newValue -}}
{{- else if $oldSet -}}
{{- $value = $oldValue -}}
{{- end -}}
{{- toYaml (dict "value" $value "warning" $warning) -}}
{{- end -}}

{{- define "mysql-operator.deployment.affinity" -}}
{{- $resolved := include "mysql-operator.resolveDeprecatedDeploymentMap" (dict "root" . "oldKey" "affinity" "newKey" "affinity") | fromYaml -}}
{{- if not (empty $resolved.value) -}}
{{- toYaml $resolved.value -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.deployment.nodeSelector" -}}
{{- $resolved := include "mysql-operator.resolveDeprecatedDeploymentMap" (dict "root" . "oldKey" "nodeSelector" "newKey" "nodeSelector") | fromYaml -}}
{{- if not (empty $resolved.value) -}}
{{- toYaml $resolved.value -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.deployment.affinityWarning" -}}
{{- $resolved := include "mysql-operator.resolveDeprecatedDeploymentMap" (dict "root" . "oldKey" "affinity" "newKey" "affinity") | fromYaml -}}
{{- $resolved.warning -}}
{{- end -}}

{{- define "mysql-operator.deployment.nodeSelectorWarning" -}}
{{- $resolved := include "mysql-operator.resolveDeprecatedDeploymentMap" (dict "root" . "oldKey" "nodeSelector" "newKey" "nodeSelector") | fromYaml -}}
{{- $resolved.warning -}}
{{- end -}}

{{- define "mysql-operator.disableLookupsWarning" -}}
{{- $disableLookups := .Values.disableLookups | default false -}}
{{- if $disableLookups -}}
{{- $lines := list
    "============================================================================================"
    "WARNING: disableLookups=true disables live-cluster lookups and validation."
    "Use this only for render-only workflows such as helm template, helm install --dry-run,"
    "or helm upgrade --install --dry-run."
    "Helm does not expose a chart-visible flag that distinguishes those workflows from real"
    "installs/upgrades."
    "If you use this for a real install or upgrade against a cluster that already contains a"
    "MySQL Operator release, Helm may render the wrong Deployment/RBAC/peering identity or"
    "skip collision and immutability checks."
    "============================================================================================" -}}
{{- $lines | join "\n" -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.deprecationWarnings" -}}
{{- $warnings := list -}}
{{- with (include "mysql-operator.deployment.affinityWarning" . | trim) -}}
  {{- $warnings = append $warnings . -}}
{{- end -}}
{{- with (include "mysql-operator.deployment.nodeSelectorWarning" . | trim) -}}
  {{- $warnings = append $warnings . -}}
{{- end -}}
{{- if gt (len $warnings) 0 -}}
{{- $warnings | join "\n" -}}
{{- end -}}
{{- end -}}

{{- define "mysql-operator.validateReservedLabels" -}}
{{- $root := .root -}}
{{- $userLabels := default (dict) .userLabels -}}
{{- $managedLabels := ((include .managedHelper $root) | fromYaml) | default (dict) -}}
{{- $overlaps := list -}}
{{- range $key := (keys $managedLabels | sortAlpha) -}}
  {{- if hasKey $userLabels $key -}}
    {{- $overlaps = append $overlaps $key -}}
  {{- end -}}
{{- end -}}
{{- if gt (len $overlaps) 0 -}}
{{- fail (printf "Invalid values: %s contains chart-managed label keys: %s" .valuePath ($overlaps | join ", ")) -}}
{{- end -}}
{{- toYaml (mergeOverwrite (dict) $userLabels $managedLabels) -}}
{{- end -}}

{{- define "mysql-operator.deploymentAnnotations" -}}
{{- $deploymentValues := default (dict) .Values.deployment -}}
{{- $userAnnotations := default (dict) $deploymentValues.deploymentAnnotations -}}
{{- $annotationKey := include "mysql-operator.topologyAnnotationKey" . -}}
{{- if hasKey $userAnnotations $annotationKey -}}
{{- fail (printf "Invalid values: deployment.deploymentAnnotations must not set chart-managed annotation %s" $annotationKey) -}}
{{- end -}}
{{- toYaml (mergeOverwrite (dict) $userAnnotations (dict $annotationKey (include "mysql-operator.requestedTopologyAnnotationValue" .))) -}}
{{- end -}}

{{- define "mysql-operator.deployment.deploymentLabels" -}}
{{- $deploymentValues := default (dict) .Values.deployment -}}
{{- include "mysql-operator.validateReservedLabels" (dict "root" . "userLabels" $deploymentValues.deploymentLabels "managedHelper" "mysql-operator.globalLabels" "valuePath" "deployment.deploymentLabels") -}}
{{- end -}}

{{- define "mysql-operator.deployment.podLabels" -}}
{{- $deploymentValues := default (dict) .Values.deployment -}}
{{- include "mysql-operator.validateReservedLabels" (dict "root" . "userLabels" $deploymentValues.podLabels "managedHelper" "mysql-operator.labels" "valuePath" "deployment.podLabels") -}}
{{- end -}}

{{/*
Stable per-operator identity labels. Services always use this selector so
same-namespace operators do not share endpoints, even when an upgraded legacy
Deployment must keep an older immutable selector.
*/}}
{{- define "mysql-operator.serviceSelectorLabels" -}}
app.kubernetes.io/name: {{ include "mysql-operator.defaultDeploymentName" . }}
app.kubernetes.io/instance: {{ include "mysql-operator.globalInstanceName" . }}
app.kubernetes.io/component: controller
{{- end }}

{{/* Global-scoped labels for objects like ClusterRole and ServiceAccount. */}}
{{- define "mysql-operator.globalLabels" -}}
{{- $serviceSelectorLabels := include "mysql-operator.serviceSelectorLabels" . | fromYaml | default (dict) -}}
{{- toYaml (mergeOverwrite (dict) $serviceSelectorLabels (dict "app.kubernetes.io/created-by" "helm" "app.kubernetes.io/managed-by" "helm")) -}}
{{- end }}

{{/*
Fresh installs use a per-operator Deployment selector derived from the resolved
deployment name. This keeps the managed "name" label while restoring the
per-operator identity that same-namespace installs need.
*/}}
{{- define "mysql-operator.freshDeploymentSelectorLabels" -}}
{{- $serviceSelectorLabels := include "mysql-operator.serviceSelectorLabels" . | fromYaml | default (dict) -}}
{{- toYaml (mergeOverwrite (dict "name" (include "mysql-operator.deploymentName" .)) $serviceSelectorLabels) -}}
{{- end }}

{{/*
Deployment and Service selectors intentionally differ during legacy upgrades:
- Deployment selectors reuse the installed immutable selector exactly.
- Service selectors always use the stable per-operator identity triplet.
*/}}
{{- define "mysql-operator.deploymentSelectorLabels" -}}
{{- $discoveredSelectorLabels := include "mysql-operator.discoveredDeploymentSelectorLabels" . | fromYaml | default (dict) -}}
{{- if gt (len $discoveredSelectorLabels) 0 -}}
{{- toYaml $discoveredSelectorLabels -}}
{{- else -}}
{{- include "mysql-operator.freshDeploymentSelectorLabels" . -}}
{{- end -}}
{{- end }}

{{- define "mysql-operator.selectorLabels" -}}
{{- $deploymentSelectorLabels := include "mysql-operator.deploymentSelectorLabels" . | fromYaml | default (dict) -}}
{{- $serviceSelectorLabels := include "mysql-operator.serviceSelectorLabels" . | fromYaml | default (dict) -}}
{{- toYaml (mergeOverwrite (dict) $deploymentSelectorLabels $serviceSelectorLabels) -}}
{{- end }}

{{- define "mysql-operator.labels" -}}
{{ include "mysql-operator.selectorLabels" . }}
version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
