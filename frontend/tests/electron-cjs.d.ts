/**
 * Electron 主进程源码是无类型声明的 CommonJS（.cjs）文件，
 * tsc 直接 import 会报 TS7016（implicit any）。
 * 这里给 *.cjs 最小 ambient 声明：默认导出为 unknown，
 * 测试内再用 `as { 具体函数签名 }` 收窄，不引入额外依赖、不放宽 strict。
 */
declare module '*.cjs' {
  const mod: unknown
  export default mod
}
