from django.urls import path
from . import views
from .views import EventListView

urlpatterns = [
    path('healthz/', views.healthz, name="healthz"),
    path('readyz/', views.readyz, name="readyz"),
    path('', views.getbook, name="getbook"),
    path('borrow/', views.borrows, name="borrows"),
    path('return/<int:pk>/', views.return_book, name="return-book"),
    path('userbo/<str:pk>/', views.userbo, name="userbo"),
    path('gens/', views.gens, name="gens"),
    path('gensr/', views.gensr, name="gensr"),
    path('genres/', views.genres_list, name="genres-list"),
    path('each/<str:pk>/', views.eachbook, name="each"),
    path('eachborrow/<str:pk>/', views.eachbobook, name="eachborrow"),
    path('author/', views.author, name="author"),
    path('autbook/<str:pk>/', views.autbook, name="autbook"),
    path('lists/', EventListView.as_view()),
    path('bookp/', views.bookp, name="bookp"),

    path('signup/', views.signup, name="signup"),
    path('signin/', views.signin, name="signin"),
    path('logout/', views.logout_view, name="logout"),
    path('verify-email/', views.verify_email, name="verify-email"),
    path('resend-verification/', views.resend_verification, name="resend-verification"),
    path('password-reset/', views.password_reset_request, name="password-reset"),
    path('password-reset-confirm/', views.password_reset_confirm, name="password-reset-confirm"),
]