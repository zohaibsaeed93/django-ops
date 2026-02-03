{{- define "djangoops.name" -}}{{ .Release.Name }}{{- end -}}
{{- define "djangoops.labels" -}}
app.kubernetes.io/name: djangoops
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: djangoops
{{- range $k, $v := .Values.labels }}
{{ $k }}: {{ $v | quote }}
{{- end }}
{{- end -}}
{{- define "djangoops.image" -}}{{ .Values.image.repository }}@{{ .Values.image.digest }}{{- end -}}
