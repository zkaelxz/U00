// DOM ids shared by <Tabs> and the pages' tab panels (kept out of Tabs.tsx for fast refresh).
export const tabButtonId = (prefix: string, id: string) => `${prefix}-tab-${id}`
export const tabPanelId = (prefix: string, id: string) => `${prefix}-panel-${id}`
