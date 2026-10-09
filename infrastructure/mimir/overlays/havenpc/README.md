# About the used Helm chart

The official Helm chart does not support monolithic mode for Mimir, which means that only microservices mode is available. For use on a local environment, this leads to a too heavy workload. 
  
For that reason we are currently using [a forked Helm chart](https://github.com/NGDATA/mimir/) which does support monolithic mode. 
  
In the official Mimir repo, there's an [open issue](https://github.com/grafana/mimir/issues/4832) to enable monolithic mode through the Helm chart. Some [comments](https://github.com/grafana/mimir/issues/4832#issuecomment-2556601445) make a lot of sense and we can map this on our own usecase as well.  
  
The folks who are behind the fork, have commented the following:
```
For those complaining about this, we are still maintaining a working fork that provides this functionality at https://github.com/NGDATA/mimir

We merge in the upstream changes every 2-3 months.
We have this setup running on several kubernetes environments without issues.
```

They are also actively working on getting their fork merged back upstream. Once that has happened, we can start using the official Helm chart. 