ENV ?= gcp
NAMESPACE ?= dmp
gcp-up:            ## 建立 GCP 基礎設施
	cd tofu/gcp && tofu init && tofu apply -var project=$(PROJECT) -var region=asia-east1
deploy:            ## 部署／升級（ENV=gcp|onprem|small）
	helm dependency update helm/altatech-dmp
	helm upgrade --install dmp helm/altatech-dmp -n $(NAMESPACE) --create-namespace -f helm/altatech-dmp/values.yaml $(if $(filter-out onprem,$(ENV)),-f helm/altatech-dmp/values-$(ENV).yaml,)
smoke:             ## 寫入 1 萬測試事件並查詢
	python3 ../backend/tools/smoke.py --namespace $(NAMESPACE)
sbom:              ## 產生 SBOM 與授權檢查（Syft + 白名單）
	syft dir:.. -o spdx-json > sbom.spdx.json && python3 ../backend/tools/license_check.py sbom.spdx.json
