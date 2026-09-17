# Керівництво: Підключення Linux-нод до LLM-кластера LAAS через Telegraf

Додаток **LAAS (Local Agent AI Station)** підтримує моніторинг розподілених вузлів кластера без використання SSH або сторонніх демонів, використовуючи стандартний промисловий агент **Telegraf (InfluxData)** та нативний Prometheus API.

---

## 1. Швидке налаштування на Linux-ноді (1 хвилина)

На будь-якому сервері Linux (Ubuntu / Debian / RHEL / Arch) з відеокартою NVIDIA:

1. Скопіюйте або завантажте скрипт [`tools/setup-telegraf-node.sh`](../tools/setup-telegraf-node.sh):
   ```bash
   chmod +x setup-telegraf-node.sh
   sudo ./setup-telegraf-node.sh
   ```

2. Або виконайте конфігурацію вручну:
   * Створіть файл `/etc/telegraf/telegraf.d/nvidia.conf`:
     ```toml
     [[inputs.nvidia_smi]]
     ```
   * У конфігурації `/etc/telegraf/telegraf.conf` або `/etc/telegraf/telegraf.d/prometheus.conf`:
     ```toml
     [[outputs.prometheus_client]]
       listen = ":9273"
       path = "/metrics"
       collectors_exclude = ["gocollector", "process"]
     ```
   * Перезапустіть службу:
     ```bash
     sudo systemctl restart telegraf
     ```

---

## 2. Перевірка роботи ендпоінта

Виконайте запит до порту 9273:
```bash
curl -s http://localhost:9273/metrics | grep nvidia_smi
```
Ви побачите стандартні метрики:
* `nvidia_smi_utilization_gpu` — завантаження GPU (%)
* `nvidia_smi_memory_used` — використана пам'ять VRAM
* `nvidia_smi_memory_total` — загальна пам'ять VRAM
* `nvidia_smi_temperature_gpu` — температура (°C)
* `nvidia_smi_power_draw` — споживання (Вт)
* `cpu_usage_active` — завантаження CPU (%)
* `mem_used` / `mem_total` — системна RAM

---

## 3. Додавання вузла в додаток LAAS

1. Відкрийте вкладку **«LLM-кластер»** у LAAS.
2. Натисніть **«+ Додати узел»**:
   * **Назва вузла:** `Remote Worker (A4000)`
   * **URL інференсу:** `http://<IP_НОДИ>:8080` (порт вашого `llama-server`)
   * **URL телеметрії:** `http://<IP_НОДИ>:9273/metrics` (порт Telegraf)
   * **Тип протоколу:** `llama_server`
3. Натисніть **«⚡ Тест з'єднання»** — система миттєво опитає вузол та виведе латентність та модель відеокарти.
4. Натисніть **«Зберегти»**.
