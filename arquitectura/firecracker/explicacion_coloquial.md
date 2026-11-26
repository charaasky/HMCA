# Contención del agente (Firecracker/KVM) — explicación coloquial

Versión natural, para explicar en un chat sin tecnicismos.

> Encerré al agente en una microVM (Firecracker, sobre KVM): sin internet y con disco de solo
> lectura. Antes usaba gVisor, que simula el kernel por software; el problema es que para
> escaparse le alcanzaba con encontrar un bug de ese kernel falso. Como Firecracker corre en
> modo real, tendría que romper el hipervisor, que es difícil. Así que, por las dudas, el host
> lo vigila por afuera con auditd: si intenta algo raro, lo bloquea al instante y queda en manos
> de un humano, que puede ver en el dashboard qué pasó y decidir si habilitarlo nuevamente o si
> hay que parchear algo. Usé 6 tipos de ataques diferentes; puedo usar más, pero es caro :D, lo
> haré el mes que viene.
