"""Network-isolated synthetic budget check, not a production workload replay."""
import json, subprocess, sys, time, uuid

def run(image, memory):
    name = 'maxibot-budget-' + uuid.uuid4().hex[:12]
    start = time.monotonic()
    try:
        subprocess.run(['docker','run','-d','--name',name,'--network','none',
            '--restart','no','--memory',memory,'--memory-swap',memory,
            '--cpus','0.5','-e','ADS_AUTOPILOT_ENABLED=false',
            '-e','ADS_WRITE_ACTIONS_ENABLED=false','--entrypoint','python',image,
            '-c','import time; payload=bytearray(320*1024*1024); time.sleep(2); print(len(payload))'],
            check=True, capture_output=True, text=True, timeout=30)
        subprocess.run(['docker','wait',name], check=True, capture_output=True, timeout=60)
        item = json.loads(subprocess.check_output(['docker','inspect',name], timeout=10))[0]
        result = {'limit':memory, 'exit_code':item['State']['ExitCode'],
                  'oom_killed':item['State']['OOMKilled'],
                  'elapsed_seconds':round(time.monotonic()-start,3),
                  'network':item['HostConfig']['NetworkMode'], 'workload':'synthetic_320MiB_allocation'}
        print(json.dumps(result), flush=True)
        return result
    finally:
        subprocess.run(['docker','rm','-f',name], capture_output=True, timeout=10)

if __name__ == '__main__':
    before=run(sys.argv[1], '256m'); after=run(sys.argv[1], '512m')
    if not (before['exit_code']==137 and before['oom_killed'] and after['exit_code']==0 and not after['oom_killed']):
        raise SystemExit('WORKER_MEMORY_BUDGET_REHEARSAL=FAIL')
    print('WORKER_MEMORY_BUDGET_REHEARSAL=PASS')
