from pathlib import Path
import sys
import types

PACKAGE_NAME = "mercari_plugin_under_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules.setdefault(PACKAGE_NAME, package)

from mercari_plugin_under_test.mercari_service import _item_from_response


def test_personal_listing_uses_standard_item_url():
    result = _item_from_response({"id": "personal-id", "name": "Personal", "price": "100", "created": "0"})

    assert result.url == "https://jp.mercari.com/item/personal-id"


def test_shops_listing_uses_shops_product_url():
    result = _item_from_response({
        "id": "shops-id",
        "name": "Shop product",
        "price": "100",
        "created": "0",
        "itemType": "ITEM_TYPE_BEYOND",
        "shop": {"id": "shop-id"},
    })

    assert result.url == "https://jp.mercari.com/shops/product/shops-id"
