# 《计算机网络》RAG 检索量化评测

该报告由 `scripts/evaluate_computer_network_rag.py` 生成，评估对象为课程知识库检索命中情况。

- 样本数：30
- 预期文档 Top-K 命中率：90.0%
- Citation 覆盖率：100.0%
- 平均延迟：78.5 ms
- P95 延迟：36.3 ms

| # | 概念 | 命中 | 状态 | 延迟(ms) | 期望文档 | Top 文档 | 问题 |
|---:|---|---|---|---:|---|---|---|
| 1 | network_architecture | 是 | ok | 1606.2 | 01_网络体系结构与分层模型.md | 00_课程大纲与学习路径.md, 08_Wireshark抓包实践.md, 01_网络体系结构与分层模型.md | 计算机网络为什么要分层？OSI 和 TCP/IP 有什么区别？ |
| 2 | network_architecture | 否 | ok | 20.7 | 01_网络体系结构与分层模型.md | 07_网络性能与故障排查.md, 11_期末复习与能力自测.md, 09_Socket编程与应用实践.md | 封装和解封装分别发生在哪些方向？ |
| 3 | physical_layer | 是 | ok | 28.3 | 02_物理层与数据链路层.md | 02_物理层与数据链路层.md, 10_协议设计与综合项目.md, 11_期末复习与能力自测.md | 物理层主要解决什么问题？ |
| 4 | data_link | 是 | ok | 30.6 | 02_物理层与数据链路层.md | 01_网络体系结构与分层模型.md, 02_物理层与数据链路层.md, 10_协议设计与综合项目.md | 数据链路层为什么需要成帧？ |
| 5 | data_link | 是 | ok | 25.7 | 02_物理层与数据链路层.md | 02_物理层与数据链路层.md, 01_网络体系结构与分层模型.md, 06_网络安全基础.md | 交换机根据什么转发以太网帧？ |
| 6 | network_ip | 是 | ok | 18.3 | 03_网络层IP与路由.md, 02_物理层与数据链路层.md | 00_课程大纲与学习路径.md, 02_物理层与数据链路层.md, 11_期末复习与能力自测.md | IP 地址和 MAC 地址的职责有什么不同？ |
| 7 | network_ip | 是 | ok | 19.5 | 03_网络层IP与路由.md | 03_网络层IP与路由.md, 01_网络体系结构与分层模型.md, 02_物理层与数据链路层.md | 路由器转发 IP 分组时会修改源 IP 吗？ |
| 8 | network_ip | 是 | ok | 28.6 | 03_网络层IP与路由.md | 03_网络层IP与路由.md, 09_Socket编程与应用实践.md, 11_期末复习与能力自测.md | CIDR 前缀长度代表什么？ |
| 9 | transport_tcp | 是 | ok | 17.6 | 04_传输层TCP与UDP.md | 04_传输层TCP与UDP.md, 12_教师教学与干预指南.md, 11_期末复习与能力自测.md | TCP 三次握手的目标是什么？ |
| 10 | transport_tcp | 是 | ok | 20.8 | 04_传输层TCP与UDP.md | 00_课程大纲与学习路径.md, 04_传输层TCP与UDP.md, 08_Wireshark抓包实践.md | 为什么 TCP 不是两次握手？ |
| 11 | transport_tcp | 是 | ok | 23.3 | 04_传输层TCP与UDP.md | 08_Wireshark抓包实践.md, 04_传输层TCP与UDP.md, 09_Socket编程与应用实践.md | SYN 报文为什么会消耗一个序列号？ |
| 12 | transport_tcp | 是 | ok | 27.6 | 04_传输层TCP与UDP.md | 04_传输层TCP与UDP.md, 12_教师教学与干预指南.md, 08_Wireshark抓包实践.md | TCP 如何通过确认号实现可靠传输？ |
| 13 | transport_udp | 否 | ok | 26.0 | 04_传输层TCP与UDP.md | 09_Socket编程与应用实践.md, 07_网络性能与故障排查.md, 11_期末复习与能力自测.md | UDP 适合哪些应用场景？ |
| 14 | transport_tcp | 是 | ok | 24.7 | 04_传输层TCP与UDP.md | 04_传输层TCP与UDP.md, 11_期末复习与能力自测.md, 07_网络性能与故障排查.md | 流量控制和拥塞控制有什么区别？ |
| 15 | application_dns | 是 | ok | 23.8 | 05_应用层协议DNS_HTTP与邮件.md | 11_期末复习与能力自测.md, 05_应用层协议DNS_HTTP与邮件.md, 00_课程大纲与学习路径.md | DNS 解析的基本过程是什么？ |
| 16 | application_http | 是 | ok | 29.3 | 05_应用层协议DNS_HTTP与邮件.md | 05_应用层协议DNS_HTTP与邮件.md, 11_期末复习与能力自测.md, 08_Wireshark抓包实践.md | HTTP 请求和响应分别包含哪些部分？ |
| 17 | application_http | 是 | ok | 26.9 | 05_应用层协议DNS_HTTP与邮件.md, 06_网络安全基础.md | 11_期末复习与能力自测.md, 00_课程大纲与学习路径.md, 08_Wireshark抓包实践.md | HTTPS 相比 HTTP 增加了什么安全能力？ |
| 18 | application_mail | 是 | ok | 22.8 | 05_应用层协议DNS_HTTP与邮件.md | 05_应用层协议DNS_HTTP与邮件.md, 11_期末复习与能力自测.md, 06_网络安全基础.md | 电子邮件为什么通常涉及 SMTP 和 POP3 或 IMAP？ |
| 19 | network_security | 是 | ok | 20.9 | 06_网络安全基础.md | 06_网络安全基础.md, 05_应用层协议DNS_HTTP与邮件.md, 02_物理层与数据链路层.md | 对称加密和非对称加密的差异是什么？ |
| 20 | network_security | 否 | ok | 36.3 | 06_网络安全基础.md | 11_期末复习与能力自测.md, 11_期末复习与能力自测.md, 08_Wireshark抓包实践.md | 数字证书解决了什么信任问题？ |
| 21 | network_security | 是 | ok | 33.7 | 06_网络安全基础.md | 06_网络安全基础.md, 11_期末复习与能力自测.md, 04_传输层TCP与UDP.md | 防火墙通常根据哪些信息过滤流量？ |
| 22 | network_performance | 是 | ok | 27.4 | 07_网络性能与故障排查.md | 07_网络性能与故障排查.md, 06_网络安全基础.md, 11_期末复习与能力自测.md | 网络性能常看哪些指标？ |
| 23 | troubleshooting | 是 | ok | 32.1 | 07_网络性能与故障排查.md | 00_课程大纲与学习路径.md, 07_网络性能与故障排查.md, 01_网络体系结构与分层模型.md | 排查网络故障时为什么要从链路到应用逐层检查？ |
| 24 | troubleshooting | 是 | ok | 23.6 | 07_网络性能与故障排查.md | 11_期末复习与能力自测.md, 05_应用层协议DNS_HTTP与邮件.md, 07_网络性能与故障排查.md | ping 和 traceroute 分别适合验证什么？ |
| 25 | wireshark | 是 | ok | 26.1 | 08_Wireshark抓包实践.md | 08_Wireshark抓包实践.md, 00_课程大纲与学习路径.md, 09_Socket编程与应用实践.md | Wireshark 中如何过滤 TCP 端口 80 的报文？ |
| 26 | wireshark | 是 | ok | 22.2 | 08_Wireshark抓包实践.md, 04_传输层TCP与UDP.md | 08_Wireshark抓包实践.md, 12_教师教学与干预指南.md, 09_Socket编程与应用实践.md | 抓包观察三次握手时要看哪些 TCP 字段？ |
| 27 | wireshark | 是 | ok | 29.9 | 08_Wireshark抓包实践.md | 08_Wireshark抓包实践.md, 10_协议设计与综合项目.md, 09_Socket编程与应用实践.md | 为什么抓包实验要记录过滤条件和观察结论？ |
| 28 | course_path | 是 | ok | 36.7 | 00_课程大纲与学习路径.md, 03_网络层IP与路由.md | 00_课程大纲与学习路径.md, 01_网络体系结构与分层模型.md, 10_协议设计与综合项目.md | 课程学习路径应该先学网络层还是传输层？ |
| 29 | course_path | 是 | ok | 21.3 | 00_课程大纲与学习路径.md, 08_Wireshark抓包实践.md | 00_课程大纲与学习路径.md, 12_教师教学与干预指南.md, 08_Wireshark抓包实践.md | 计算机网络课程最终应能完成哪些实践任务？ |
| 30 | troubleshooting | 是 | ok | 23.9 | 07_网络性能与故障排查.md, 05_应用层协议DNS_HTTP与邮件.md | 00_课程大纲与学习路径.md, 11_期末复习与能力自测.md, 07_网络性能与故障排查.md | 如果 HTTP 访问很慢，应该如何结合 DNS、TCP 和链路层排查？ |

说明：该脚本只衡量检索阶段。正式答辩建议继续人工抽查最终回答的正确率、引用忠实度和未验证声明比例。
