{{- define "mysql-innodbcluster.disableLookupsWarning" -}}
{{- $disableLookups := .Values.disableLookups | default false -}}
{{- if $disableLookups -}}
{{- $lines := list
    "============================================================================================"
    "WARNING: disableLookups=true disables live-cluster lookups and validation."
    "Use this only for render-only workflows such as helm template, helm install --dry-run,"
    "or helm upgrade --install --dry-run."
    "Helm does not expose a chart-visible flag that distinguishes those workflows from real"
    "installs/upgrades."
    "If you use this for a real install or upgrade against a cluster that already contains"
    "MySQL InnoDBCluster resources or referenced Secrets, Helm may skip Secret existence/key"
    "validation and render manifests that do not match live cluster state."
    "============================================================================================" -}}
{{- $lines | join "\n" -}}
{{- end -}}
{{- end -}}
