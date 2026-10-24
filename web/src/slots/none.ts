/** 开源构建的扩展入口：什么都不注册。带扩展的构建经 KNOWWORK_WEB_EXTENSIONS 换成扩展包的入口。 */
import type { Register } from './registry';

export function install(register: Register): void {
  void register;
}
