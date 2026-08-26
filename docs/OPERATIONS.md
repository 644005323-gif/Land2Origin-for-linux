# Operations

Origin and the VM are long-lived services. Do not restart, relicense, alter
hosts, or replace the production Wine prefix as part of a plotting request.

The external runner serializes jobs with a file lock because one shared Origin
automation session cannot safely process concurrent jobs. A failed job keeps
its staging directory in `/home/sd1/Desktop/Origin-VM-Share/jobs/<run_id>` and
writes a failed archive record for diagnosis.

The production external script avoids spawning Wine `reg.exe` when the
IOApplication proxy registration is already present. This matters on the
3.5 GiB VM where Origin is resident and free memory is limited.

Useful checks:

```bash
ssh origin-vm 'pgrep -af Origin64.exe; free -h'
ls -la "/home/sd1/Nutstore Files/表征数据 - 同步副本/电化学/<run_id>"
cat "/home/sd1/Nutstore Files/表征数据 - 同步副本/电化学/<run_id>/complete.json"
```
