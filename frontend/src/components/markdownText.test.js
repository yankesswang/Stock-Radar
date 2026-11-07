import { describe, expect, it } from 'vitest'

import { clean, headingId } from './markdownText'


describe('headingId', () => {
  it('creates stable unicode-safe anchors', () => {
    expect(headingId('## 風險 / Risk `2026`')).toBe('h-風險-risk-2026')
  })
})

describe('clean', () => {
  it('removes frontmatter and converts Obsidian callouts', () => {
    const markdown = '---\ntitle: Test\n---\n> [!warning] 風險\n內容'
    expect(clean(markdown)).toBe('> ⚠️ **風險**\n內容')
  })

  it('removes raw HTML anchors', () => {
    expect(clean('<a id="item-1"></a>保留內容')).toBe('保留內容')
  })
})
