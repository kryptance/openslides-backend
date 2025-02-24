import copy
import cProfile
import os
from abc import abstractmethod
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from dependency_injector import providers
from requests.models import Response as RequestsResponse

from openslides_backend.http.application import OpenSlidesBackendWSGIApplication
from openslides_backend.http.views import ActionView, PresenterView
from openslides_backend.http.views.base_view import ROUTE_OPTIONS_ATTR, RouteFunction
from openslides_backend.services.datastore.adapter import DatastoreAdapter
from openslides_backend.services.keycloak.interface import IdpAdminService
from openslides_backend.services.media.interface import MediaService
from openslides_backend.services.vote.adapter import VoteAdapter
from openslides_backend.services.vote.interface import VoteService
from openslides_backend.shared.env import Environment, is_truthy
from openslides_backend.shared.exceptions import MediaServiceException
from openslides_backend.shared.interfaces.services import Services
from openslides_backend.shared.interfaces.wsgi import Headers, View
from openslides_backend.wsgi import OpenSlidesBackendServices, OpenSlidesBackendWSGI
from tests.util import Response


def convert_to_test_response(response: RequestsResponse) -> Response:
    """Helper function to convert a requests Response to a TestResponse."""
    return Response(
        response.iter_content(),
        str(response.status_code),
        Headers({**dict(response.headers), "Content-Type": "application/json"}),
        MagicMock(),
    )


class TestVoteService(VoteService):
    url: str

    @abstractmethod
    def vote(self, data: dict[str, Any]) -> Response: ...


class TestVoteAdapter(VoteAdapter, TestVoteService):
    def vote(self, data: dict[str, Any]) -> Response:
        data_copy = copy.deepcopy(data)
        del data_copy["id"]
        response = self.make_request(
            self.url.replace("internal", "system") + f"?id={data['id']}",
            data_copy,
        )
        return convert_to_test_response(response)

class TestIdpAdminAdapter(IdpAdminService):
    def set_authentication(self, access_token: str, refresh_id: str) -> None:
        pass

    def create_user(self, username: str, idp_id: str | None) -> str:
        return username + f"_{idp_id}" if idp_id is not None else ""


def create_action_test_application() -> OpenSlidesBackendWSGIApplication:
    return create_test_application(ActionView)


def create_presenter_test_application() -> OpenSlidesBackendWSGIApplication:
    return create_test_application(PresenterView)


def create_base_test_application(
    view: type[View], services: Services, env: Environment | None = None
) -> OpenSlidesBackendWSGIApplication:
    if not env:
        env = Environment(os.environ)
    application_factory = OpenSlidesBackendWSGI(
        env=env, logging=MagicMock(), view=view, services=services
    )
    application = application_factory.setup()

    return application


def create_test_application(view: type[View]) -> OpenSlidesBackendWSGIApplication:
    env = Environment(os.environ)
    services = OpenSlidesBackendServices(
        config=env.get_service_url(),
        logging=MagicMock(),
    )
    services.vote = providers.Singleton(
        TestVoteAdapter, services.config.vote_url, MagicMock()
    )
    mock_media_service = Mock(MediaService)
    mock_media_service.upload_mediafile = Mock(
        side_effect=side_effect_for_upload_method
    )
    services.media = MagicMock(return_value=mock_media_service)
    services.idp_admin = providers.Singleton(TestIdpAdminAdapter)

    return create_base_test_application(view, services, env)


def side_effect_for_upload_method(
    file: str, id: int, mimetype: str, **kwargs: Any
) -> None:
    # Check against encoded version of "Do me a favour and trigger a mock mediaservice error, will you?"
    if (
        file
        == "RG8gbWUgYSBmYXZvdXIgYW5kIHRyaWdnZXIgYSBtb2NrIG1lZGlhc2VydmljZSBlcnJvciwgd2lsbCB5b3U/"
    ):
        raise MediaServiceException("Mocked error on media service upload")


def get_route_path(route_function: RouteFunction, name: str = "") -> str:
    route_options_list = getattr(route_function, ROUTE_OPTIONS_ATTR)
    for route_options in route_options_list:
        if route_options["raw_path"].endswith(name):
            return route_options["raw_path"]
    raise ValueError(f"Route {name} does not exist")


def mock_datastore_method(method: str, verbose: bool = False) -> tuple[Mock, Any]:
    """
    Patches the given method of the DatastoreAdapter and returns the created mock as well as the
    patcher.
    """
    orig_method = getattr(DatastoreAdapter, method)

    def mock_method(inner_self: DatastoreAdapter, *args: Any, **kwargs: Any) -> Any:
        if verbose:
            print(orig_method.__name__, args, kwargs)
        return orig_method(inner_self, *args, **kwargs)

    patcher = patch.object(DatastoreAdapter, method, autospec=True)
    mock = patcher.start()
    mock.side_effect = mock_method
    return mock, patcher


def disable_dev_mode(fn: Callable) -> Callable:
    return patch(
        "openslides_backend.shared.env.Environment.is_dev_mode",
        MagicMock(return_value=False),
    )(fn)


def performance(func: Callable) -> Callable:
    return pytest.mark.skipif(
        not is_truthy(os.environ.get("OPENSLIDES_PERFORMANCE_TESTS", "")),
        reason="Performance tests are disabled.",
    )(func)


class Profiler:
    """
    Helper class to profile a block of code. Use as context manager and provide filename to save the
    output to.
    """

    def __init__(self, filename: str) -> None:
        self.filename = filename

    def __enter__(self) -> None:
        self.profiler = cProfile.Profile()
        self.profiler.enable()

    def __exit__(self, *args: Any, **kwargs: Any) -> None:
        self.profiler.disable()
        self.profiler.dump_stats(self.filename)


class CountDatastoreCalls:
    """
    Helper class to track the amount of datastore calls (= cache misses). Use as context manager and
    access the result via the `count` property.
    """

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose

    def __enter__(self) -> "CountDatastoreCalls":
        self.patcher: list[Any] = []
        self.mocks: list[Mock] = []
        for method in ("get", "get_many"):
            mock, patcher = mock_datastore_method(method, self.verbose)
            self.mocks.append(mock)
            self.patcher.append(patcher)
        return self

    def __exit__(self, *args: Any, **kwargs: Any) -> None:
        for patcher in self.patcher:
            patcher.stop()

    @property
    def calls(self) -> int:
        return sum(mock.call_count for mock in self.mocks)
