/**
 * 组件测试写法范例（T-035 设施验收用）。
 *
 * 约定：
 * - 渲染断言用 @testing-library/jest-dom 的 matcher（toBeInTheDocument 等）；
 * - 用户交互用 @testing-library/user-event 触发真实事件序列，不直接调用 props 回调；
 * - 查询优先按角色（getByRole），与用户感知方式一致，避免依赖实现细节的 class；
 * - 纯函数逻辑仍写在 *.test.ts 里，组件测试只覆盖渲染与交互；
 * - 通用 mock（scrollIntoView / IntersectionObserver / matchMedia）放 tests/setup.ts。
 */
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { Button } from '../src/ui'

describe('jsdom 测试环境', () => {
  it('navigator.userAgent 带 jsdom 标识，DOM 文档可用', () => {
    // 作为 jsdom 环境生效的可观测证据；Node 原生环境没有 document
    expect(window.navigator.userAgent).toMatch(/jsdom/)
    expect(document).toBeInstanceOf(Document)
  })
})

describe('Button 组件（组件测试范例）', () => {
  it('渲染文本并反映禁用态', () => {
    render(<Button disabled>提交</Button>)
    const button = screen.getByRole('button', { name: '提交' })
    expect(button).toBeInTheDocument()
    expect(button).toBeDisabled()
  })

  it('user-event 点击触发 onClick', async () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>保存</Button>)

    await userEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('禁用状态下点击不触发 onClick', async () => {
    const onClick = vi.fn()
    render(
      <Button disabled onClick={onClick}>
        保存
      </Button>,
    )

    await userEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(onClick).not.toHaveBeenCalled()
  })

  it('渲染节点供下一个用例验证 cleanup', () => {
    render(<Button>临时节点</Button>)
    expect(screen.getByRole('button', { name: '临时节点' })).toBeInTheDocument()
  })

  it('上一用例的 DOM 已被 setup.ts 的 afterEach(cleanup) 清理', () => {
    expect(screen.queryByRole('button', { name: '临时节点' })).toBeNull()
    expect(document.body).toBeEmptyDOMElement()
  })

  it('ghost 变体保持透明底默认样式（T-037：局部修复不得改全局 ghost）', () => {
    render(<Button variant="ghost">静默操作</Button>)
    const btn = screen.getByRole('button', { name: '静默操作' })
    expect(btn.className).toContain('bg-transparent')
    expect(btn.className).not.toContain('bg-slate-700')
  })
})
