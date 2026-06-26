from django.urls import path
from django.conf import settings
from django.conf.urls.static import static

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("edit/<str:session_id>/", views.editor, name="editor"),

    # API
    path("api/upload/", views.api_upload, name="api_upload"),
    path("api/import/", views.api_import, name="api_import"),
    path("api/save-frame/<str:session_id>/", views.api_save_frame, name="api_save_frame"),
    path("api/reprocess-frame/<str:session_id>/", views.api_reprocess_frame, name="api_reprocess_frame"),
    path("api/export/<str:session_id>/", views.api_export, name="api_export"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
