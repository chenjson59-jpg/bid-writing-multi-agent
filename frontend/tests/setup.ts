/**
 * Vitest 全局测试夹具（默认 jsdom 环境）。
 * 由 vite.config.ts 的 test.setupFiles 注册，每个测试文件执行前运行一次。
 * 新增 jsdom 缺失 API 的统一 mock 也集中放在本文件，不要散落在各组件测试里。
 * 标注 `// @vitest-environment node` 的纯 Node 测试（如 electron_trust.test.ts）
 * 也会加载本文件，因此 DOM 相关 mock 必须先做环境判断。
 */
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
// vitest 专用入口：注册 jest-dom matcher（toBeInTheDocument / toBeDisabled 等）并补齐 expect 类型
import '@testing-library/jest-dom/vitest'

// globals:false 下 Testing Library 不会自动注册 cleanup，
// 显式在每个用例后卸载组件，防止 DOM 与事件监听跨用例泄漏（node 环境下为空操作）。
afterEach(() => {
  cleanup()
})

// 以下 mock 仅在 jsdom 环境生效；node 环境没有 DOM 全局对象，直接跳过。
if (typeof window !== 'undefined' && typeof Element !== 'undefined') {
  // jsdom 没有真实布局引擎，Element.prototype.scrollIntoView 只是一个会输出
  // 「Not implemented」警告的桩；WritePage 定位任务卡片时会调用它。
  // 统一替换为确定性空操作，组件测试需要断言调用时再用 vi.spyOn 包装。
  Element.prototype.scrollIntoView = function scrollIntoView(): void {
    // intentionally empty：jsdom 无法模拟滚动，滚动行为属手动验证范围
  }

  // jsdom 不提供 IntersectionObserver（懒加载/可见性观察会用到），
  // 给一个最小构造器桩，避免组件渲染期引用未定义全局对象而抛 ReferenceError。
  class MockIntersectionObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): IntersectionObserverEntry[] {
      return []
    }
  }
  globalThis.IntersectionObserver =
    MockIntersectionObserver as unknown as typeof IntersectionObserver

  // jsdom 不实现 window.matchMedia（响应式断点/暗色模式查询会用到），
  // 返回固定 matches:false 的 MediaQueryList 桩；需要模拟断点变化的用例可自行 vi.spyOn。
  if (typeof window.matchMedia !== 'function') {
    window.matchMedia = (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList
  }
}
