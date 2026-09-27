{{- define "aperture.labels" -}}
app.kubernetes.io/name: aperture
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/* Selector labels: include "aperture.selector" (list . "backend") */}}
{{- define "aperture.selector" -}}
app.kubernetes.io/name: aperture
app.kubernetes.io/instance: {{ (index . 0).Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end }}

{{/* Pod security context for a fixed non-root uid: include "aperture.podSecurity" 10001 */}}
{{- define "aperture.podSecurity" -}}
runAsNonRoot: true
runAsUser: {{ . }}
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "aperture.containerSecurity" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: [ALL]
{{- end }}

{{- define "aperture.llmHostIP" -}}
{{- $ip := required "llm.hostIP is required: the IP of host.minikube.internal" .Values.llm.hostIP -}}
{{- if not (regexMatch `^([0-9]{1,3}\.){3}[0-9]{1,3}$` $ip) -}}
{{- fail (printf "llm.hostIP must be an IPv4 address, got %q" $ip) -}}
{{- end -}}
{{- $ip -}}
{{- end }}
