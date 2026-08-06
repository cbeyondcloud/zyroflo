// D1: the same endpoint as urls.js declares, reached inline under a different name and
// with a different path-variable spelling. norm_path must erase BOTH differences, or the
// graph carries two endpoint nodes for one endpoint and every impact query splits.
const detailPath = '/orders/v1/detail/{id}';

export function OrderDetail(id) {
  return fetch(detailPath.replace('{id}', id)).then((r) => r.json());
}
