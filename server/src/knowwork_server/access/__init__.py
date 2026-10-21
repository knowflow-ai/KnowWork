"""工作空间、组织结构与 RBAC。

授权判定交给 Casbin（带域 RBAC，域 = 工作空间），组织结构与成员关系存自己的表，
继承关系以 g 规则同步进 Casbin；数据隔离靠 PostgreSQL 行级安全。
"""
