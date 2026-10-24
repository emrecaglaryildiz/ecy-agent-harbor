// This trusted initializer is never exposed as a model tool.
exports.default = async ({ page }) => {
  await page.context().route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!['http:', 'https:'].includes(url.protocol) || !['GET', 'HEAD'].includes(request.method()))
      return route.abort('blockedbyclient');
    return route.continue();
  });
  if (page.context().routeWebSocket)
    await page.context().routeWebSocket('**/*', socket => socket.close());
};
