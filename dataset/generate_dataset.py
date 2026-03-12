"""
dataset/generate_dataset.py - Generates 3 training datasets for the SLA Monitor agents.
Run: python dataset/generate_dataset.py
"""
import pandas as pd
import numpy as np
import random
from pathlib import Path

RANDOM_SEED = 42
np.random.seed(RANDOM_SEED)
random.seed(RANDOM_SEED)
OUTPUT_DIR = Path(__file__).parent

def rn(mu, sigma, lo, hi):
    return float(np.clip(np.random.normal(mu, sigma), lo, hi))

def ri(lo, hi):
    return random.randint(lo, hi)

PHASE_MAP = {"Running":0,"Pending":1,"Failed":2,"CrashLoopBackOff":3,"Unknown":4}

def generate_pod_health_dataset(n=5000):
    rows = []
    states = ["Normal","Warning","SLA_Violation","Failed"]
    for _ in range(n):
        s = random.choices(states, weights=[0.50,0.20,0.18,0.12])[0]
        if s == "Normal":
            cpu,mem,restarts,resp,err = rn(30,10,2,65),rn(35,10,5,70),random.choices([0,1,2],[0.85,0.12,0.03])[0],rn(300,80,50,700),rn(0.5,0.5,0,2)
            phase,ready,oom,net_errs,disk = "Running",True,False,ri(0,3),False
        elif s == "Warning":
            cpu,mem,restarts,resp,err = rn(72,8,60,88),rn(75,8,65,90),random.choices([1,2,3],[0.5,0.35,0.15])[0],rn(1500,300,800,2200),rn(3.5,1.0,1.5,6)
            phase = random.choices(["Running","Pending"],[0.85,0.15])[0]
            ready,oom,net_errs,disk = random.choices([True,False],[0.8,0.2])[0],random.choices([False,True],[0.9,0.1])[0],ri(3,15),random.choices([False,True],[0.85,0.15])[0]
        elif s == "SLA_Violation":
            cpu,mem,restarts,resp,err = rn(85,6,78,99),rn(88,5,80,99),random.choices([2,3,4,5],[0.25,0.35,0.25,0.15])[0],rn(2500,500,2000,4500),rn(7,2,5,15)
            phase = random.choices(["Running","Pending","Unknown"],[0.6,0.3,0.1])[0]
            ready,oom,net_errs,disk = random.choices([True,False],[0.5,0.5])[0],random.choices([False,True],[0.6,0.4])[0],ri(10,40),random.choices([False,True],[0.6,0.4])[0]
        else:
            cpu = random.choices([rn(95,3,90,100),rn(5,5,0,15)],[0.5,0.5])[0]
            mem,restarts,resp,err = rn(95,4,88,100),random.choices([3,4,5,6,10],[0.15,0.2,0.3,0.2,0.15])[0],random.choices([rn(5000,1000,3000,8000),0],[0.6,0.4])[0],rn(25,10,10,100)
            phase = random.choices(["Failed","CrashLoopBackOff","Unknown","Pending"],[0.35,0.35,0.2,0.1])[0]
            ready,oom,net_errs,disk = False,random.choices([False,True],[0.3,0.7])[0],ri(20,100),random.choices([False,True],[0.4,0.6])[0]
        rows.append({"cpu_percent":round(cpu,2),"memory_percent":round(mem,2),"restart_count":restarts,
                     "response_time_ms":round(resp,2),"error_rate_percent":round(err,2),"pod_phase":phase,
                     "pod_phase_encoded":PHASE_MAP.get(phase,4),"container_ready":int(ready),"oom_killed":int(oom),
                     "network_errors":net_errs,"disk_pressure":int(disk),"pod_state_label":s})
    return pd.DataFrame(rows)

FAILURE_TYPES=["CrashLoopBackOff","OOMKilled","HighCPU","HighMemory","PodPending","NetworkError","ContainerNotReady","DiskPressure","ConfigError","UnknownFailure"]
RECOVERY_ACTIONS=["restart_pod","rollout_restart_deployment","scale_up_replicas","adjust_resource_limits","fix_configuration","drain_node","clear_disk","no_action_monitor"]
F2A={"CrashLoopBackOff":("rollout_restart_deployment",0.70),"OOMKilled":("adjust_resource_limits",0.75),"HighCPU":("scale_up_replicas",0.65),"HighMemory":("adjust_resource_limits",0.70),"PodPending":("scale_up_replicas",0.60),"NetworkError":("restart_pod",0.65),"ContainerNotReady":("restart_pod",0.70),"DiskPressure":("clear_disk",0.75),"ConfigError":("fix_configuration",0.80),"UnknownFailure":("no_action_monitor",0.50)}

def ffeat(ft):
    if ft=="CrashLoopBackOff": return rn(40,20,5,90),rn(50,20,5,95),ri(3,15),rn(60,15,20,85),ri(1,3),rn(30,10,10,80)
    elif ft=="OOMKilled": return rn(50,20,10,85),rn(95,3,88,100),ri(1,8),rn(70,10,40,90),ri(1,4),rn(15,8,2,50)
    elif ft=="HighCPU": return rn(92,5,82,100),rn(55,15,20,80),ri(0,3),rn(85,8,60,99),ri(1,5),rn(5,3,0,20)
    elif ft=="HighMemory": return rn(60,15,20,85),rn(91,5,82,100),ri(0,4),rn(80,10,55,99),ri(1,4),rn(6,3,0,25)
    elif ft=="PodPending": return rn(85,8,70,100),rn(82,8,65,100),ri(0,2),rn(50,20,10,80),ri(2,8),rn(8,4,0,30)
    elif ft in("NetworkError","ContainerNotReady"): return rn(35,15,5,70),rn(40,15,5,75),ri(1,5),rn(65,15,30,90),ri(1,3),rn(20,8,5,60)
    elif ft=="DiskPressure": return rn(45,15,10,75),rn(55,15,15,85),ri(0,3),rn(75,10,50,95),ri(1,3),rn(4,2,0,15)
    elif ft=="ConfigError": return rn(20,10,2,50),rn(25,10,2,55),ri(2,10),rn(40,15,10,70),ri(1,3),rn(50,15,20,90)
    else: return rn(50,25,2,100),rn(50,25,2,100),ri(0,8),rn(65,20,10,99),ri(1,5),rn(12,8,0,50)

def generate_recovery_dataset(n=4000):
    rows=[]
    amap={a:i for i,a in enumerate(RECOVERY_ACTIONS)}
    fmap={f:i for i,f in enumerate(FAILURE_TYPES)}
    for _ in range(n):
        ft=random.choice(FAILURE_TYPES)
        primary,prob=F2A[ft]
        action=primary if random.random()<prob else random.choice([a for a in RECOVERY_ACTIONS if a!=primary])
        cpu,mem,restarts,uptime,replicas,err=ffeat(ft)
        rows.append({"failure_type":ft,"failure_type_encoded":fmap[ft],"cpu_percent":round(cpu,2),"memory_percent":round(mem,2),
                     "restart_count":restarts,"uptime_percent":round(uptime,2),"replica_count":replicas,
                     "error_rate_percent":round(err,2),"recovery_action":action,"recovery_action_encoded":amap[action]})
    return pd.DataFrame(rows)

def generate_autoscale_dataset(n=3000):
    rows=[]
    for _ in range(n):
        action=random.choices(["scale_up","scale_down","no_change"],[0.35,0.25,0.40])[0]
        if action=="scale_up": cpu,mem,rps,rep,resp,queue=rn(85,7,70,100),rn(80,8,65,100),rn(800,150,400,1500),ri(1,5),rn(2200,400,1500,5000),ri(50,500)
        elif action=="scale_down": cpu,mem,rps,rep,resp,queue=rn(15,8,1,35),rn(20,8,1,40),rn(50,30,1,150),ri(3,10),rn(150,50,50,400),ri(0,20)
        else: cpu,mem,rps,rep,resp,queue=rn(50,15,20,75),rn(50,15,20,75),rn(300,100,100,600),ri(2,6),rn(600,150,200,1200),ri(5,80)
        rows.append({"cpu_percent":round(cpu,2),"memory_percent":round(mem,2),"requests_per_sec":round(rps,2),
                     "current_replicas":rep,"response_time_ms":round(resp,2),"queue_depth":queue,"scale_action":action})
    return pd.DataFrame(rows)

if __name__=="__main__":
    print("Generating datasets...\n")
    df1=generate_pod_health_dataset(5000)
    df1.to_csv(OUTPUT_DIR/"pod_health_dataset.csv",index=False)
    print(f"[1] pod_health_dataset.csv → {len(df1)} rows\n{df1['pod_state_label'].value_counts().to_string()}\n")
    df2=generate_recovery_dataset(4000)
    df2.to_csv(OUTPUT_DIR/"recovery_action_dataset.csv",index=False)
    print(f"[2] recovery_action_dataset.csv → {len(df2)} rows\n{df2['recovery_action'].value_counts().to_string()}\n")
    df3=generate_autoscale_dataset(3000)
    df3.to_csv(OUTPUT_DIR/"autoscale_dataset.csv",index=False)
    print(f"[3] autoscale_dataset.csv → {len(df3)} rows\n{df3['scale_action'].value_counts().to_string()}\n")
    print("Done.")
